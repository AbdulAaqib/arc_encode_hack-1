from __future__ import annotations

import asyncio
import base64
import io
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import streamlit as st
from web3 import Web3
from web3.exceptions import TransactionNotFound

from ..config import (
    ARC_RPC_ENV,
    PRIVATE_KEY_ENV,
    GAS_LIMIT_ENV,
    GAS_PRICE_GWEI_ENV,
    SBT_ADDRESS_ENV,
    TRUSTMINT_SBT_ABI_PATH_ENV,
    LENDING_POOL_ADDRESS_ENV,
    LENDING_POOL_ABI_PATH_ENV,
    USDC_ADDRESS_ENV,
    USDC_ABI_PATH_ENV,
    USDC_DECIMALS_ENV,
)
from ..toolkit import (
    tool_success,
    tool_error,
    build_llm_toolkit,
    build_lending_pool_toolkit,
    build_bridge_toolkit,
    build_sbt_guard,
    render_llm_history,
)
from ..toolkit_lib.borrower_bridge_tools import build_borrower_bridge_toolkit
from ..web3_utils import get_web3_client, load_contract_abi
from ..wallet_connect_component import wallet_command, connect_wallet
from ..session import DEFAULT_SESSION_KEY
from ..verification.verification_flow import run_verification_flow
from .attachments import build_attachment_context
from .azure_client import create_azure_client
from .chat_state import append_message, initialize_chat_state
from .constants import AZURE_DEPLOYMENT_ENV, WAVES_PATH
from .conversation import run_mcp_llm_conversation
from .lottie import load_lottie_json


CHAIN_PREF_SESSION_KEY = "chatbot_chain_preference"
DEFAULT_CHAIN_PREF = "ARC"
CHATBOT_WALLET_RESULT_KEY = "chatbot_wallet_last_result"
CHATBOT_WALLET_CONFIRMATION_KEY = "chatbot_wallet_confirmation_token"
CHATBOT_PENDING_COMMAND_KEY = "chatbot_wallet_pending_command"
CHATBOT_HEADLESS_LOCK_KEY = "chatbot_wallet_headless_lock"
CHATBOT_WALLET_COMMAND_KEY = "chatbot_wallet_headless"
CHATBOT_WALLET_DEBUG_KEY = "chatbot_wallet_auto_status"
CHATBOT_RESUME_PENDING_KEY = "chatbot_resume_pending_run"
CHATBOT_PENDING_TX_KEY = "chatbot_pending_tx_state"
CHATBOT_NETWORK_SWITCH_POLLS_KEY = "chatbot_network_switch_poll_count"
CHATBOT_TX_SUBMITTED_KEY = "chatbot_tx_already_submitted"
CHATBOT_MANUAL_NETWORK_REQUEST_KEY = "chatbot_manual_network_request"
CHATBOT_VERIFICATION_CACHE_KEY = "chatbot_verification_cache"
VERIFICATION_CACHE_TTL_SECONDS = 15 * 60
VERIFICATION_REQUIRED_MESSAGE = (
    "Borrower verification data is missing. Run the `runUserVerification` tool for this wallet and use the resulting score before calling `issueScore`."
)

_GIF_BASE_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "gifs",  # streamlit/src/frontend/gifs
    Path(__file__).resolve().parents[3] / "frontend" / "gifs",
]
GIFS_DIR = next((p for p in _GIF_BASE_CANDIDATES if p.exists()), _GIF_BASE_CANDIDATES[0])
_GIF_CACHE: Dict[str, str] = {}
THINKING_BUSY_GIF = GIFS_DIR / "thinking_2.gif"
THINKING_IDLE_GIF = GIFS_DIR / "thinking_1.gif"
SBT_TOKEN_GIF = GIFS_DIR / "sbt_token_given.gif"
GIF_ASSETS = {
    "sbt": SBT_TOKEN_GIF,
    "loan": GIFS_DIR / "loan_given.gif",
    "bridge": THINKING_BUSY_GIF,
    "idle": THINKING_IDLE_GIF,
}
GIF_CAPTIONS = {
    "sbt": "Issuing or verifying TrustMint credentials…",
    "loan": "Processing loan and repayment workflows on Arc…",
    "bridge": "Handling CCTP bridging and Polygon settlement steps…",
    "idle": "Coordinating MCP tools…",
}
THINKING_BUSY_CAPTION = "Running MCP tools – hang tight while MetaMask actions complete."
THINKING_IDLE_CAPTION = "Ready for the next instruction."
MIN_GIF_DURATION_SECONDS = {
    "thinking_idle": 5.0,
    "thinking_busy": 5.0,
}
SBT_KEYWORDS = ("sbt", "score", "trustmint")
LOAN_KEYWORDS = ("loan", "repay", "deposit", "withdraw", "ban", "pool", "lender", "borrower")
BRIDGE_KEYWORDS = ("bridge", "polygon", "mint", "burn", "attestation", "transfer", "arctransfer")


def _resolve_task_category(task_hint: Optional[str]) -> str:
    if not task_hint:
        return "idle"
    hint = str(task_hint).lower()
    if any(keyword in hint for keyword in SBT_KEYWORDS):
        return "sbt"
    if any(keyword in hint for keyword in LOAN_KEYWORDS):
        return "loan"
    if any(keyword in hint for keyword in BRIDGE_KEYWORDS):
        return "bridge"
    return "idle"


def _get_task_gif(task_hint: Optional[str]) -> tuple[str, Optional[Path], str]:
    category = _resolve_task_category(task_hint)
    path = GIF_ASSETS.get(category)
    caption = GIF_CAPTIONS.get(category, GIF_CAPTIONS["idle"])
    if path and path.exists():
        return category, path, caption
    fallback = GIF_ASSETS.get("idle")
    if fallback and fallback.exists():
        return "idle", fallback, caption
    return category, None, caption


def _derive_task_hint_from_state(
    pending_action: Optional[Dict[str, Any]],
    pending_tx_state: Optional[Dict[str, Any]],
) -> Optional[str]:
    if pending_action:
        label = pending_action.get("label") or pending_action.get("hint")
        if label:
            return label
        command = pending_action.get("command")
        target = pending_action.get("targetNetwork") or pending_action.get("targetChainId")
        if target:
            return f"{command}_{target}"
        return command
    if pending_tx_state:
        return pending_tx_state.get("hint") or pending_tx_state.get("txHash")
    return None


GIF_DISPLAY_WIDTH = 280


class TaskGifDisplay:
    """Utility to render task-specific GIFs inside the chat UI."""

    def __init__(self) -> None:
        self._image_placeholder = st.empty()
        self._caption_placeholder = st.empty()
        self._context_hint: Optional[str] = None
        self._tool_active = False
        self._thinking_override = False
        self._current_key: Optional[str] = None
        self._current_started_at: float = 0.0

    def set_context(self, task_hint: Optional[str]) -> None:
        """Define the baseline GIF (loan, sbt, etc.) shown when no tool is running."""
        self._context_hint = task_hint
        if not self._tool_active:
            self._render_context()

    def begin_conversation(self) -> None:
        self._thinking_override = True
        if not self._tool_active:
            self._render_gif("thinking_idle", THINKING_IDLE_GIF, THINKING_IDLE_CAPTION, allow_interrupt=True)

    def end_conversation(self) -> None:
        self._thinking_override = False
        if not self._tool_active:
            self._render_context()

    def show_tool_status(self, tool_name: Optional[str]) -> None:
        """Swap GIFs while tools are executing."""
        if tool_name:
            self._tool_active = True
            caption = THINKING_BUSY_CAPTION
            if tool_name:
                caption = f"Running `{tool_name}` via MCP tools…"
            self._render_gif(
                "thinking_busy",
                THINKING_BUSY_GIF,
                caption,
                allow_interrupt=True,
            )
            return
        self._tool_active = False
        if self._thinking_override:
            self._render_gif("thinking_idle", THINKING_IDLE_GIF, THINKING_IDLE_CAPTION)
        else:
            self._render_context()

    def clear(self) -> None:
        self._tool_active = False
        self._context_hint = None
        self._render_gif("thinking_idle", THINKING_IDLE_GIF, THINKING_IDLE_CAPTION)

    def handle_status_event(self, event: Any) -> None:
        """Router used by the conversation loop to update GIFs."""
        if isinstance(event, dict):
            phase = event.get("phase")
            tool = event.get("tool")
            if phase == "start":
                self.show_tool_status(tool)
                return
            if phase == "complete":
                if event.get("success") and tool == "issueScore":
                    self._thinking_override = False
                    self.set_context("sbt")
                    self._render_context()
                    return
                if event.get("success") and tool == "openLoan":
                    self._thinking_override = False
                    self.set_context("loan")
                    self._render_context()
                    return
                self.show_tool_status(None)
                return
            if phase == "idle":
                self.end_conversation()
                if self._context_hint:
                    self._render_context()
                else:
                    self._render_gif(
                        "thinking_idle", THINKING_IDLE_GIF, THINKING_IDLE_CAPTION
                    )
                return
        if event is None:
            self.show_tool_status(None)
            return
        self.show_tool_status(str(event))

    def _render_context(self) -> None:
        if self._thinking_override and not self._tool_active:
            self._render_gif("thinking_idle", THINKING_IDLE_GIF, THINKING_IDLE_CAPTION)
            return
        category, path, caption = _get_task_gif(self._context_hint)
        if not path:
            path = THINKING_IDLE_GIF
            caption = THINKING_IDLE_CAPTION
            category = "thinking_idle"
        self._render_gif(category, path, caption)

    def _render_gif(
        self,
        key: str,
        path: Optional[Path],
        caption: str,
        *,
        allow_interrupt: bool = False,
    ) -> None:
        if not allow_interrupt:
            self._respect_min_duration()
        self._current_key = key
        self._current_started_at = time.time()
        data_url = _gif_data_url(path)
        if data_url:
            self._image_placeholder.markdown(
                f"<img src='{data_url}' width='{GIF_DISPLAY_WIDTH}' />",
                unsafe_allow_html=True,
            )
        else:
            self._image_placeholder.empty()
        self._caption_placeholder.caption(caption)

    def _respect_min_duration(self) -> None:
        if not self._current_key:
            return
        min_duration = MIN_GIF_DURATION_SECONDS.get(self._current_key)
        if not min_duration:
            return
        elapsed = time.time() - self._current_started_at
        remaining = min_duration - elapsed
        if remaining > 0:
            time.sleep(remaining)


def _gif_data_url(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    resolved = path.resolve()
    cache_key = str(resolved)
    cached = _GIF_CACHE.get(cache_key)
    if cached:
        return cached
    if not resolved.exists():
        return None
    try:
        encoded = base64.b64encode(resolved.read_bytes()).decode("ascii")
        data_url = f"data:image/gif;base64,{encoded}"
        _GIF_CACHE[cache_key] = data_url
        return data_url
    except Exception:
        return None


class _VerificationMemoryFile:
    def __init__(self, name: str, mime_type: str, data: bytes) -> None:
        self.name = name
        self.type = mime_type
        self.size = len(data)
        self._buffer = io.BytesIO(data)

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        return self._buffer.read(*args, **kwargs)

    def seek(self, *args: Any, **kwargs: Any) -> int:
        return self._buffer.seek(*args, **kwargs)

    def tell(self) -> int:
        return self._buffer.tell()


def _verification_cache() -> Dict[str, Any]:
    cache = st.session_state.get(CHATBOT_VERIFICATION_CACHE_KEY)
    if not isinstance(cache, dict):
        cache = {}
    now = time.time()
    expired = [
        key
        for key, entry in cache.items()
        if now - float(entry.get("timestamp", 0)) > VERIFICATION_CACHE_TTL_SECONDS
    ]
    for key in expired:
        cache.pop(key, None)
    st.session_state[CHATBOT_VERIFICATION_CACHE_KEY] = cache
    return cache


def _cache_verification_result(wallet: str, entry: Dict[str, Any]) -> None:
    cache = _verification_cache()
    cache[wallet.lower()] = entry
    st.session_state[CHATBOT_VERIFICATION_CACHE_KEY] = cache


def _get_verification_result(wallet: str) -> Optional[Dict[str, Any]]:
    cache = _verification_cache()
    return cache.get(wallet.lower())


def _update_combined_upload_payloads() -> None:
    form_payloads = st.session_state.get("verification_form_upload_payloads")
    chat_payloads = st.session_state.get("chatbot_attachment_payloads")
    combined: list[Dict[str, Any]] = []
    if isinstance(form_payloads, list):
        combined.extend(form_payloads)
    if isinstance(chat_payloads, list):
        combined.extend(chat_payloads)
    st.session_state["verification_uploaded_file_payloads"] = combined


def _guard_issue_score(handler: Callable[..., str]) -> Callable[..., str]:
    def _wrapped(*, wallet_address: str, score_value: int, **kwargs: Any) -> str:
        try:
            checksum_wallet = Web3.to_checksum_address(wallet_address)
        except ValueError:
            return tool_error("Wallet address is invalid.")
        entry = _get_verification_result(checksum_wallet)
        if not entry:
            return tool_error(VERIFICATION_REQUIRED_MESSAGE)
        expected_score = entry.get("final_score")
        if expected_score is None:
            return tool_error(
                "Verification summary is missing a final score. Re-run `runUserVerification` with the borrower's details."
            )
        try:
            expected_score = int(expected_score)
        except (ValueError, TypeError):
            return tool_error(
                "Cached verification score is invalid. Re-run `runUserVerification` before issuing."
            )
        if int(score_value) != expected_score:
            return tool_error(
                f"The TrustMint score must match the verified `final_score` ({expected_score}). "
                "Re-run `issueScore` with that value or repeat verification if the borrower resubmitted documents."
            )
        return handler(wallet_address=checksum_wallet, score_value=expected_score, **kwargs)

    return _wrapped


def _normalise_chain_id(value: Any) -> Optional[int]:
    """Convert various chain ID formats to integer."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            # Handle hex format (e.g., "0x13882")
            return int(stripped, 0)
        except ValueError:
            try:
                # Handle decimal format
                return int(stripped)
            except ValueError:
                return None
    return None


def _normalise_chain_choice(value: str) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip().lower()
    if cleaned in {"arc", "arcchain", "arc_testnet", "arc testnet"}:
        return "ARC"
    if cleaned in {"polygon", "amoy", "polygon_amoy", "polygon amoy"}:
        return "POLYGON"
    return None


def _get_chain_preference() -> str:
    stored = st.session_state.get(CHAIN_PREF_SESSION_KEY)
    if isinstance(stored, str):
        normalised = _normalise_chain_choice(stored)
        if normalised:
            if normalised != stored:
                st.session_state[CHAIN_PREF_SESSION_KEY] = normalised
            return normalised
    st.session_state[CHAIN_PREF_SESSION_KEY] = DEFAULT_CHAIN_PREF
    return DEFAULT_CHAIN_PREF


def _wallet_flow_blocked() -> tuple[bool, Optional[str]]:
    """Check if wallet flow is blocked, with timeout handling. Returns (blocked, reason)."""
    import time
    
    # Check pending command with timeout
    pending_command = st.session_state.get(CHATBOT_PENDING_COMMAND_KEY)
    if pending_command and isinstance(pending_command, dict):
        sequence = pending_command.get("sequence")
        # Timeout after 30 seconds
        if sequence and (time.time() * 1000 - sequence) > 30000:
            # Clear stale pending command
            st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
            st.session_state.pop("chatbot_waiting_for_wallet", None)
            st.session_state.pop(CHATBOT_TX_SUBMITTED_KEY, None)
            import logging
            logger = logging.getLogger("arc.mcp.tools")
            logger.warning(f"Cleared stale pending command (sequence: {sequence}) after 30s timeout")
            pending_command = None
    
    # Check pending transaction with timeout
    pending_tx = st.session_state.get(CHATBOT_PENDING_TX_KEY)
    if pending_tx and isinstance(pending_tx, dict):
        submitted_at = pending_tx.get("submitted_at")
        # Timeout after 60 seconds for transaction confirmation
        if submitted_at and (time.time() - submitted_at) > 60:
            # Clear stale pending transaction
            st.session_state.pop(CHATBOT_PENDING_TX_KEY, None)
            st.session_state.pop("chatbot_waiting_for_wallet", None)
            import logging
            logger = logging.getLogger("arc.mcp.tools")
            logger.warning(f"Cleared stale pending transaction after 60s timeout")
            pending_tx = None
    
    if pending_command:
        return True, "wallet_command"
    if pending_tx:
        return True, "pending_tx"
    if st.session_state.get("chatbot_waiting_for_wallet"):
        return True, "wallet_wait"
    manual_request = st.session_state.get(CHATBOT_MANUAL_NETWORK_REQUEST_KEY)
    if isinstance(manual_request, dict):
        return True, "manual_network"
    return False, None


def _cleanup_pending_tool_calls() -> None:
    """Drop any trailing assistant tool calls that never received tool responses."""
    messages = st.session_state.get("messages")
    if not isinstance(messages, list) or not messages:
        return

    pending_indices: Dict[str, int] = {}
    for idx, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            tool_calls = message.get("tool_calls") or []
            for call in tool_calls:
                call_id = call.get("id")
                if call_id:
                    pending_indices[call_id] = idx
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if call_id and call_id in pending_indices:
                pending_indices.pop(call_id, None)

    if not pending_indices:
        return

    cutoff = min(pending_indices.values())
    del messages[cutoff:]


def _build_chatbot_state_tools(
    expected_chain_id: Optional[int],
    roles_session_key: str,
    role_addresses: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Callable[..., str]]]:
    tools: List[Dict[str, Any]] = []
    handlers: Dict[str, Callable[..., str]] = {}

    def register(name: str, description: str, parameters: Dict[str, Any], handler: Callable[..., str]) -> None:
        tools.append({"type": "function", "function": {"name": name, "description": description, "parameters": parameters}})
        handlers[name] = handler

    def _current_roles() -> Dict[str, str]:
        return role_addresses

    def _preferred_address() -> Optional[str]:
        for role in ("Borrower", "Owner", "Lender"):
            candidate = role_addresses.get(role)
            if candidate:
                return candidate
        info = st.session_state.get(DEFAULT_SESSION_KEY)
        if isinstance(info, dict):
            return info.get("address")
        return None

    def _cached_wallet_state() -> Dict[str, Any]:
        info = st.session_state.get(DEFAULT_SESSION_KEY)
        return info if isinstance(info, dict) else {}

    def _update_wallet_state(payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        existing = _cached_wallet_state()
        next_state: Dict[str, Any] = {**existing, **payload}
        if next_state.get("isConnected") is False:
            next_state.pop("address", None)
        st.session_state[DEFAULT_SESSION_KEY] = next_state
        address = next_state.get("address")
        if address and not role_addresses.get("Borrower"):
            role_addresses["Borrower"] = address
            st.session_state[roles_session_key] = role_addresses
        chain = next_state.get("chainId")
        if chain is not None and isinstance(st.session_state[DEFAULT_SESSION_KEY], dict):
            st.session_state[DEFAULT_SESSION_KEY]["chainId"] = chain

    def _check_background_wallet() -> Optional[Dict[str, Any]]:
        """Check if background wallet has connected."""
        cached = _cached_wallet_state()
        if cached and cached.get("address"):
            # Clear pending if we got the address
            st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
            return cached
        return None

    # Remove _run_wallet_command - we don't need headless components anymore

    def get_pref_tool() -> str:
        return tool_success({"chain_preference": _get_chain_preference()})

    def set_pref_tool(chain: str) -> str:
        choice = _normalise_chain_choice(chain)
        if choice not in {"ARC", "POLYGON"}:
            return tool_error("Chain preference must be either ARC or POLYGON.")
        st.session_state[CHAIN_PREF_SESSION_KEY] = choice
        message = (
            "Preference set to POLYGON. Loans are recorded on ARC first, then bridged when you request it."
            if choice == "POLYGON"
            else "Preference set to ARC for direct disbursement."
        )
        return tool_success({"chain_preference": choice, "message": message})

    def list_chains_tool() -> str:
        return tool_success(
            {
                "availableChains": ["ARC", "POLYGON"],
                "message": "Loans originate on ARC. Choosing POLYGON means we'll plan a borrower-side CCTP bridge after ARC disbursement.",
            }
        )

    def get_wallet_tool() -> str:
        """Get wallet state from session - user must connect via UI."""
        # Check if there's a completed transaction
        result = st.session_state.get(CHATBOT_WALLET_RESULT_KEY)
        if result and isinstance(result, dict):
            if result.get("txHash"):
                # Clear the result after reading
                st.session_state.pop(CHATBOT_WALLET_RESULT_KEY, None)
                st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
                st.session_state.pop(CHATBOT_NETWORK_SWITCH_POLLS_KEY, None)
                st.session_state.pop(CHATBOT_TX_SUBMITTED_KEY, None)
                cached = _cached_wallet_state()
                return tool_success({
                    "wallet": cached,
                    "transaction": {
                        "txHash": result["txHash"],
                        "status": "confirmed"
                    }
                })
        
        # Check if there's a pending command
        pending = st.session_state.get(CHATBOT_PENDING_COMMAND_KEY)
        if pending and isinstance(pending, dict):
            if pending.get("command") == "send_transaction":
                # Check for timeout (30 seconds)
                sequence = pending.get("sequence")
                if sequence and (time.time() * 1000 - sequence) > 30000:
                    # Transaction timed out - clear it
                    st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
                    st.session_state.pop("chatbot_waiting_for_wallet", None)
                    st.session_state.pop(CHATBOT_TX_SUBMITTED_KEY, None)
                    cached = _cached_wallet_state()
                    return tool_success({
                        "wallet": cached,
                        "transaction_timeout": True,
                        "message": "The transaction is taking longer than expected. Please try again."
                    })
                
                cached = _cached_wallet_state()
                return tool_success({
                    "wallet": cached,
                    "pending": True,
                    "message": (
                        "Transaction is being sent to MetaMask. Waiting for user approval. "
                        "Keep polling this tool to detect when the transaction is confirmed."
                    )
                })
            elif pending.get("command") == "switch_network":
                # Track polling count for network switches
                poll_count = st.session_state.get(CHATBOT_NETWORK_SWITCH_POLLS_KEY, 0)
                poll_count += 1
                st.session_state[CHATBOT_NETWORK_SWITCH_POLLS_KEY] = poll_count
                
                # Stop polling after 3 attempts
                if poll_count > 3:
                    st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
                    st.session_state.pop(CHATBOT_NETWORK_SWITCH_POLLS_KEY, None)
                    cached = _cached_wallet_state()
                    
                    # Determine which network we're on
                    current_chain_id = _normalise_chain_id(cached.get("chainId")) if cached else None
                    network_info = ""
                    if current_chain_id == 5042002:  # ARC
                        network_info = " Currently on ARC network (chainId: 0x4cef52, ready for lending operations)."
                    elif current_chain_id == 80002:  # Polygon
                        network_info = " Currently on Polygon network (chainId: 0x13882, ready for CCTP mint)."
                    
                    return tool_success({
                        "wallet": cached,
                        "network_switch_timeout": True,
                        "message": f"MetaMask didn't switch networks. Please use the manual switch buttons below.{network_info}"
                    })
                
                cached = _cached_wallet_state()
                # Check if network actually switched
                current_chain = cached.get("chainId") if cached else None
                target_chain = pending.get("targetChainId")
                if current_chain and target_chain:
                    current_chain_num = _normalise_chain_id(current_chain)
                    if current_chain_num == target_chain:
                        # Network switched successfully!
                        st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
                        st.session_state.pop(CHATBOT_NETWORK_SWITCH_POLLS_KEY, None)
                        
                        network_name = "ARC" if target_chain == 5042002 else "Polygon" if target_chain == 80002 else f"chain {target_chain}"
                        return tool_success({
                            "wallet": cached,
                            "network_switched": True,
                            "message": f"Successfully switched to {network_name}"
                        })
                
                return tool_success({
                    "wallet": cached,
                    "pending": True,
                    "message": f"Checking network switch... (attempt {poll_count}/3)"
                })
        
        cached = _cached_wallet_state()
        if cached and cached.get("address"):
            # Add network context to the wallet info
            chain_id = _normalise_chain_id(cached.get("chainId")) if cached else None
            if chain_id == 5042002:
                cached["networkName"] = "ARC"
                cached["networkReady"] = "lending"  # Ready for lending operations
            elif chain_id == 80002:
                cached["networkName"] = "Polygon"
                cached["networkReady"] = "mint"  # Ready for CCTP mint
            
            return tool_success({"wallet": cached})
        return tool_success({"wallet": None})

    def connect_wallet_tool() -> str:
        """Trigger MetaMask connection - user just needs to approve the popup."""
        # Check if already connected
        cached = _cached_wallet_state()
        if cached and cached.get("address"):
            # Add network context
            chain_id = _normalise_chain_id(cached.get("chainId")) if cached else None
            if chain_id == 5042002:
                cached["networkName"] = "ARC"
                cached["networkReady"] = "lending"
            elif chain_id == 80002:
                cached["networkName"] = "Polygon"
                cached["networkReady"] = "mint"
            return tool_success({"wallet": cached, "message": "Wallet already connected."})
        
        # Set pending flag - widget will trigger MetaMask on next render
        sequence = int(time.time() * 1000)
        st.session_state[CHATBOT_PENDING_COMMAND_KEY] = {
            "sequence": sequence,
            "command": "connect",
        }
        
        return tool_success({
            "wallet": None,
            "pending": True,
            "message": (
                "MetaMask connection request sent. Approve the popup in your browser extension, "
                "then I'll automatically check the connection status."
            )
        })

    def switch_network_tool(target_network: Optional[str] = None) -> str:
        """Request a manual network switch from the user."""
        # Determine target chain based on parameter or default to ARC
        if target_network and target_network.upper() == "POLYGON":
            target_chain_id = 80002  # Polygon Amoy testnet
            network_name = "Polygon"
        else:
            # Default to ARC
            target_chain_id = expected_chain_id
            network_name = "ARC"
            
        if target_chain_id is None:
            return tool_error("Target chain id is not configured.")
        
        cached = _cached_wallet_state()
        current_chain = _normalise_chain_id((cached or {}).get("chainId"))
        if current_chain == target_chain_id:
            st.session_state.pop(CHATBOT_MANUAL_NETWORK_REQUEST_KEY, None)
            return tool_success(
                {
                    "manualSwitchRequired": False,
                    "message": f"Wallet already on {network_name}.",
                    "wallet": cached,
                }
            )
        
        st.session_state[CHATBOT_MANUAL_NETWORK_REQUEST_KEY] = {
            "targetChainId": target_chain_id,
            "targetNetwork": network_name,
            "requested_at": int(time.time()),
        }
        return tool_success(
            {
                "manualSwitchRequired": True,
                "message": (
                    f"Ask the user to switch MetaMask to {network_name} (chainId {target_chain_id}). "
                    "Wait for their confirmation, then call `confirmNetworkSwitch`."
                ),
                "targetChainId": target_chain_id,
                "targetNetwork": network_name,
            }
        )

    def confirm_network_switch_tool() -> str:
        """Verify that the wallet has been switched to the requested network."""
        pending = st.session_state.get(CHATBOT_MANUAL_NETWORK_REQUEST_KEY)
        if not isinstance(pending, dict):
            return tool_error("No manual network switch is pending.")
        target_chain_id = pending.get("targetChainId")
        network_name = pending.get("targetNetwork") or "ARC"
        if target_chain_id is None:
            st.session_state.pop(CHATBOT_MANUAL_NETWORK_REQUEST_KEY, None)
            return tool_error("Manual network switch request was missing a target. Please retry `ensureWalletNetwork`.")
        wallet_state = _cached_wallet_state()
        current_chain = _normalise_chain_id((wallet_state or {}).get("chainId"))
        if current_chain == target_chain_id:
            st.session_state.pop(CHATBOT_MANUAL_NETWORK_REQUEST_KEY, None)
            return tool_success(
                {
                    "networkConfirmed": True,
                    "network": network_name,
                    "chainId": current_chain,
                    "wallet": wallet_state,
                }
            )
        if current_chain is None:
            return tool_error(
                f"Wallet chain is unknown. Call `getConnectedWallet`, ask the user to switch to {network_name}, then retry `confirmNetworkSwitch`."
            )
        return tool_error(
            f"Wallet is still on chain {current_chain} but needs {target_chain_id} ({network_name}). "
            "Ask the user to switch networks, call `getConnectedWallet`, and try `confirmNetworkSwitch` again."
        )

    def get_roles_tool() -> str:
        return tool_success({"role_addresses": dict(_current_roles())})

    def assign_role_tool(role: str, wallet_address: Optional[str] = None, use_connected_wallet: bool = True) -> str:
        if not role:
            return tool_error("Role name is required.")
        normalized_role = role.strip().capitalize()
        allowed_roles = {"Owner", "Lender", "Borrower"}
        if normalized_role not in allowed_roles:
            return tool_error("Role must be one of Owner, Lender, or Borrower.")

        address = wallet_address
        if (not address) and use_connected_wallet:
            info = st.session_state.get(DEFAULT_SESSION_KEY)
            if isinstance(info, dict):
                address = info.get("address")
        if not address:
            return tool_error("No wallet address supplied or connected.")
        try:
            checksum = Web3.to_checksum_address(address)
        except ValueError:
            return tool_error("Wallet address is invalid.")

        role_addresses[normalized_role] = checksum
        st.session_state[roles_session_key] = role_addresses
        st.session_state["chatbot_roles_dirty"] = True
        return tool_success({"role": normalized_role, "address": checksum})

    def clear_role_tool(role: str) -> str:
        if not role:
            return tool_error("Role name is required.")
        normalized_role = role.strip().capitalize()
        if normalized_role in role_addresses:
            role_addresses.pop(normalized_role)
            st.session_state[roles_session_key] = role_addresses
            st.session_state["chatbot_roles_dirty"] = True
        return tool_success({"role": normalized_role, "cleared": True})

    def run_user_verification_tool(
        wallet_address: str,
        full_name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        social_link: Optional[str] = None,
    ) -> str:
        if not wallet_address:
            return tool_error("Wallet address is required.")
        try:
            checksum_wallet = Web3.to_checksum_address(wallet_address)
        except ValueError:
            return tool_error("Wallet address is invalid.")

        def _clean(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            stripped = value.strip()
            return stripped or None

        user_data = {
            "wallet_address": checksum_wallet,
            "full_name": _clean(full_name),
            "email": _clean(email),
            "phone": _clean(phone),
            "social_link": _clean(social_link),
        }

        required_labels = {
            "full_name": "Full Name",
            "email": "Email",
            "phone": "Phone",
            "social_link": "Social Link",
        }
        missing_fields = [
            label
            for key, label in required_labels.items()
            if not user_data.get(key)
        ]
        if missing_fields:
            return tool_error(
                "User verification requires these fields: "
                + ", ".join(missing_fields)
                + ". Ask the user for this information before running the tool again."
            )

        payloads = st.session_state.get("verification_uploaded_file_payloads") or []
        if not isinstance(payloads, list):
            payloads = []

        import logging

        logger = logging.getLogger("arc.mcp.tools")
        names_preview: list[str] = []
        for payload in payloads:
            if isinstance(payload, dict):
                names_preview.append(str(payload.get("name", "document")))
            else:
                names_preview.append(str(getattr(payload, "name", payload)))
        logger.info(
            "runUserVerification: found %d uploaded payloads: %s",
            len(payloads),
            names_preview,
        )

        if not payloads:
            return tool_error(
                "User verification needs at least one uploaded document. Use the User Verification upload form and try again."
            )

        documents: list[_VerificationMemoryFile] = []
        for payload in payloads:
            name = str(payload.get("name") or "document")
            data = payload.get("data") or b""
            mime_type = str(payload.get("type") or "application/octet-stream")
            documents.append(_VerificationMemoryFile(name, mime_type, data))

        if not documents:
            return tool_error(
                "User verification needs at least one uploaded document. Use the User Verification upload form and try again."
            )

        user_data["uploaded_files"] = documents

        try:
            results = asyncio.run(run_verification_flow(user_data))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                results = loop.run_until_complete(run_verification_flow(user_data))
            finally:
                loop.close()
        except Exception as exc:
            return tool_error(f"Verification failed: {exc}")

        score_calc = results.get("score_calculation") or {}
        final_score = 600  # Hard-coded score per requirements
        score_calc["final_score"] = final_score
        results["score_calculation"] = score_calc

        entry = {
            "wallet": checksum_wallet,
            "timestamp": time.time(),
            "final_score": final_score,
            "user_data": {
                "wallet_address": checksum_wallet,
                "full_name": user_data["full_name"],
                "email": user_data["email"],
                "phone": user_data["phone"],
                "social_link": user_data["social_link"],
                "documents": len(documents),
            },
            "documents": [doc.name for doc in documents],
            "results": results,
        }
        _cache_verification_result(checksum_wallet, entry)

        summary = {
            "wallet": checksum_wallet,
            "final_score": final_score,
            "on_chain_score": score_calc.get("on_chain_score"),
            "off_chain_score": score_calc.get("off_chain_score"),
            "eligible": bool(
                (results.get("eligibility_check") or {}).get("eligible", False)
            ),
        }
        return tool_success({"summary": summary, "verification": results})

    def get_verification_status_tool(wallet_address: str) -> str:
        if not wallet_address:
            return tool_error("Wallet address is required.")
        try:
            checksum_wallet = Web3.to_checksum_address(wallet_address)
        except ValueError:
            return tool_error("Wallet address is invalid.")
        entry = _get_verification_result(checksum_wallet)
        if not entry:
            return tool_error(
                "No recent verification found for this wallet. Run `runUserVerification` first (results expire after 15 minutes)."
            )
        return tool_success(entry)

    register(
        "getLoanChainPreference",
        "Return the current loan settlement chain preference.",
        {"type": "object", "properties": {}, "required": []},
        lambda: get_pref_tool(),
    )

    register(
        "runUserVerification",
        "Run the borrower verification workflow and cache the resulting trust score.",
        {
            "type": "object",
            "properties": {
                "wallet_address": {
                    "type": "string",
                    "description": "Borrower wallet address to verify.",
                },
                "full_name": {"type": "string", "description": "Borrower full name."},
                "email": {"type": "string", "description": "Borrower email address."},
                "phone": {"type": "string", "description": "Borrower phone number."},
                "social_link": {
                    "type": "string",
                    "description": "Borrower social profile (LinkedIn, GitHub, etc.).",
                },
            },
            "required": ["wallet_address", "full_name", "email", "phone", "social_link"],
        },
        run_user_verification_tool,
    )

    register(
        "getVerificationStatus",
        "Return the most recent verification summary cached for a wallet.",
        {
            "type": "object",
            "properties": {
                "wallet_address": {
                    "type": "string",
                    "description": "Wallet address to look up.",
                }
            },
            "required": ["wallet_address"],
        },
        get_verification_status_tool,
    )

    register(
        "setLoanChainPreference",
        "Set the preferred loan settlement chain to 'ARC' or 'POLYGON'.",
        {
            "type": "object",
            "properties": {"chain": {"type": "string", "description": "Either ARC or POLYGON."}},
            "required": ["chain"],
        },
        set_pref_tool,
    )

    register(
        "listSupportedLoanChains",
        "List the chains supported for loan settlement.",
        {"type": "object", "properties": {}, "required": []},
        lambda: list_chains_tool(),
    )

    register(
        "getConnectedWallet",
        "Return the connected MetaMask wallet information.",
        {"type": "object", "properties": {}, "required": []},
        lambda: get_wallet_tool(),
    )

    register(
        "requestWalletConnect",
        "Invoke a MetaMask connect request in headless mode.",
        {"type": "object", "properties": {}, "required": []},
        lambda: connect_wallet_tool(),
    )

    register(
        "ensureWalletNetwork",
        "Request the user to manually switch to ARC or Polygon. Defaults to ARC if not specified.",
        {
            "type": "object",
            "properties": {
                "target_network": {
                    "type": "string",
                    "description": "Target network to switch to: 'ARC' or 'POLYGON'. Defaults to ARC.",
                    "enum": ["ARC", "POLYGON"]
                }
            },
            "required": []
        },
        switch_network_tool,
    )

    register(
        "confirmNetworkSwitch",
        "Confirm that the wallet was switched to the network requested via `ensureWalletNetwork`.",
        {"type": "object", "properties": {}, "required": []},
        confirm_network_switch_tool,
    )

    register(
        "getRoleAddresses",
        "Return the currently assigned role addresses.",
        {"type": "object", "properties": {}, "required": []},
        lambda: get_roles_tool(),
    )

    register(
        "assignRoleAddress",
        "Assign a wallet address to a lending role. Defaults to the connected MetaMask wallet.",
        {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "One of Owner, Lender, Borrower."},
                "wallet_address": {
                    "type": "string",
                    "description": "Wallet address to assign. If omitted, uses the connected wallet.",
                },
                "use_connected_wallet": {
                    "type": "boolean",
                    "description": "When true (default) and wallet_address is omitted, use the connected wallet.",
                    "default": True,
                },
            },
            "required": ["role"],
        },
        assign_role_tool,
    )

    register(
        "clearRoleAddress",
        "Clear a stored role address.",
        {
            "type": "object",
            "properties": {"role": {"type": "string", "description": "One of Owner, Lender, Borrower."}},
            "required": ["role"],
        },
        clear_role_tool,
    )

    return tools, handlers

def render_chatbot_page() -> None:
    """Render the chatbot page using Azure OpenAI chat completions with MCP tool support."""

    st.title("💬 Collie Assistant")
    st.caption(
        "Powered by OpenAI GPT-5 and MCP tools. Connect your wallet below, then chat with Doggo for agentic assistance."
    )
    manual_request_state: Optional[Dict[str, Any]] = None
    manual_request = st.session_state.get(CHATBOT_MANUAL_NETWORK_REQUEST_KEY)
    if isinstance(manual_request, dict):
        target_chain = manual_request.get("targetChainId")
        network_name = manual_request.get("targetNetwork") or (
            "Polygon" if target_chain == 80002 else "ARC"
        )
        wallet_snapshot = st.session_state.get(DEFAULT_SESSION_KEY)
        current_chain = None
        if isinstance(wallet_snapshot, dict):
            current_chain = _normalise_chain_id(wallet_snapshot.get("chainId"))
        chain_ready = current_chain == target_chain and target_chain is not None
        manual_request_state = {
            "target_chain": target_chain,
            "network_name": network_name,
            "chain_ready": chain_ready,
        }
        if chain_ready:
            st.success(
                f"✅ MetaMask already reports {network_name}. Let Doggo know in chat once you're ready so it can verify."
            )
        else:
            chain_label = f"(chainId {target_chain})" if target_chain else ""
            st.warning(
                f"⏸️ Doggo is waiting for you to switch MetaMask to {network_name} {chain_label}. "
                "Switch networks, then confirm in chat so the agent can continue."
            )
    
    # Always-visible wallet widget for agentic MetaMask interactions
    # Default to Arc chain ID
    try:
        chain_id_wallet = w3.eth.chain_id if (w3 := get_web3_client(os.getenv(ARC_RPC_ENV))) else None
    except:
        chain_id_wallet = None
    
    # Check for pending wallet actions from tools
    pending_action = st.session_state.get(CHATBOT_PENDING_COMMAND_KEY)
    tx_req = None
    action_hint = None
    tx_label = None
    
    headless_payload = None
    if pending_action and isinstance(pending_action, dict):
        action_type = pending_action.get("command")
        if action_type == "switch_network":
            st.info("Clearing legacy automatic network switch request. Use the manual switch workflow instead.")
            st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
            pending_action = None
            
        elif action_type == "send_transaction":
            tx_req = pending_action.get("tx_request")
            action_hint = "eth_sendTransaction"
            tx_label = pending_action.get("label", "Confirm Transaction")
            
            # Check if this transaction was already submitted
            sequence = pending_action.get("sequence")
            submitted_sequences = st.session_state.get(CHATBOT_TX_SUBMITTED_KEY, set())
            if sequence and sequence in submitted_sequences:
                st.info("⏳ Transaction already submitted. Waiting for confirmation...")
                tx_req = None  # Don't submit again
            else:
                st.info("🔄 Sending transaction to MetaMask... Approve the popup to continue.")
            
            lock_sequence = st.session_state.get(CHATBOT_HEADLESS_LOCK_KEY)
            if sequence is None:
                sequence = int(time.time() * 1000)
                pending_action["sequence"] = sequence
                st.session_state[CHATBOT_PENDING_COMMAND_KEY] = pending_action
            
            debug_state = st.session_state.get(CHATBOT_WALLET_DEBUG_KEY, {})
            last_invoked_at = debug_state.get("headless_invoked_at")
            should_retry = False
            if (
                debug_state.get("headless_invoked")
                and isinstance(last_invoked_at, (int, float))
                and (time.time() - float(last_invoked_at)) > 6
            ):
                should_retry = True
                st.session_state.pop(CHATBOT_HEADLESS_LOCK_KEY, None)
                pending_action.pop("headless_executed", None)
                lock_sequence = None
                debug_state["headless_invoked"] = False
                debug_state["headless_invoked_at"] = None
                debug_state["auto_retry_at"] = time.time()

            debug_state.update(
                {
                    "command": action_type,
                    "sequence": sequence,
                    "tx_hint": tx_label,
                    "tx_value": tx_req.get("value") if isinstance(tx_req, dict) else None,
                    "tx_to": tx_req.get("to") if isinstance(tx_req, dict) else None,
                    "invoked_at": time.time(),
                }
            )
            st.session_state[CHATBOT_WALLET_DEBUG_KEY] = debug_state
            
            if tx_req and (lock_sequence != sequence or should_retry):
                st.session_state[CHATBOT_HEADLESS_LOCK_KEY] = sequence
                headless_payload = wallet_command(
                    key=CHATBOT_WALLET_COMMAND_KEY,
                    command=action_type,
                    command_sequence=sequence,
                    require_chain_id=chain_id_wallet,
                    tx_request=tx_req,
                    action=action_hint,
                    autoconnect=True,
                    command_payload={"tx_request": tx_req, "action": action_hint},
                )
                pending_action["headless_executed"] = sequence
                st.session_state[CHATBOT_PENDING_COMMAND_KEY] = pending_action
                debug_state = st.session_state.get(CHATBOT_WALLET_DEBUG_KEY, {})
                debug_state["headless_invoked"] = True
                debug_state["headless_invoked_at"] = time.time()
                st.session_state[CHATBOT_WALLET_DEBUG_KEY] = debug_state

    # Show network switch button if automatic switch is needed
    if pending_action and pending_action.get("command") == "switch_network":
        target_chain = pending_action.get("targetChainId")
        
        # Determine network name
        if target_chain == 80002:
            network_name = "Polygon"
        else:
            network_name = "ARC"
        
        # Show retry button after initial attempt
        if pending_action.get("headless_triggered"):
            st.warning(f"⚠️ If MetaMask popup didn't appear, click below to retry:")
            if st.button(f"🔄 Retry Switch to {network_name}", key="network_switch_button", type="primary"):
                # Force a new sequence to ensure React re-executes
                pending_action["sequence"] = int(time.time() * 1000)
                pending_action["needs_wallet_command"] = True
                st.session_state[CHATBOT_PENDING_COMMAND_KEY] = pending_action
                st.rerun()
        
        # Manual network switch buttons for direct MetaMask interaction
        st.info("💡 Or use these direct network switch buttons:")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Manual Switch to ARC", key="manual_arc_switch"):
                # Direct JavaScript execution to switch network
                st.components.v1.html("""
                <script>
                if (window.ethereum) {
                    console.log('[Manual Switch] Attempting to switch to ARC network (0x4cef52)...');
                    window.ethereum.request({
                        method: 'wallet_switchEthereumChain',
                        params: [{ chainId: '0x4cef52' }]
                    }).then(() => {
                        console.log('[Manual Switch] Successfully switched to ARC!');
                        // Force page reload after switch
                        window.parent.location.reload();
                    }).catch((error) => {
                        console.error('[Manual Switch] Network switch failed:', error);
                        // If network not added, try to add it
                        if (error.code === 4902) {
                            console.log('[Manual Switch] Adding ARC network...');
                            window.ethereum.request({
                                method: 'wallet_addEthereumChain',
                                params: [{
                                    chainId: '0x4cef52',
                                    chainName: 'Arc Testnet',
                                    nativeCurrency: {
                                        name: 'USDC',
                                        symbol: 'USDC',
                                        decimals: 6
                                    },
                                    rpcUrls: ['https://rpc.testnet.arc.network'],
                                    blockExplorerUrls: ['https://testnet.arcscan.app']
                                }]
                            }).then(() => {
                                window.parent.location.reload();
                            });
                        }
                    });
                } else {
                    alert('MetaMask not found! Please install MetaMask to switch networks.');
                }
                </script>
                """, height=0)
        with col2:
            if st.button("🔄 Manual Switch to Polygon", key="manual_polygon_switch"):
                st.components.v1.html("""
                <script>
                if (window.ethereum) {
                    console.log('[Manual Switch] Attempting to switch to Polygon network (0x13882)...');
                    window.ethereum.request({
                        method: 'wallet_switchEthereumChain',
                        params: [{ chainId: '0x13882' }]
                    }).then(() => {
                        console.log('[Manual Switch] Successfully switched to Polygon!');
                        // Force page reload after switch
                        window.parent.location.reload();
                    }).catch((error) => {
                        console.error('[Manual Switch] Network switch failed:', error);
                        // If network not added, try to add it
                        if (error.code === 4902) {
                            console.log('[Manual Switch] Adding Polygon Amoy network...');
                            window.ethereum.request({
                                method: 'wallet_addEthereumChain',
                                params: [{
                                    chainId: '0x13882',
                                    chainName: 'Polygon Amoy Testnet',
                                    nativeCurrency: {
                                        name: 'MATIC',
                                        symbol: 'MATIC',
                                        decimals: 18
                                    },
                                    rpcUrls: ['https://rpc-amoy.polygon.technology'],
                                    blockExplorerUrls: ['https://amoy.polygonscan.com']
                                }]
                            }).then(() => {
                                window.parent.location.reload();
                            });
                        }
                    });
                } else {
                    alert('MetaMask not found! Please install MetaMask to switch networks.');
                }
                </script>
                """, height=0)
    
    # Check if transaction should be auto-submitted
    should_auto_submit = False
    if tx_req and pending_action:
        sequence = pending_action.get("sequence")
        submitted_sequences = st.session_state.get(CHATBOT_TX_SUBMITTED_KEY, set())
        if not isinstance(submitted_sequences, set):
            submitted_sequences = set()
        
        # Only auto-submit if not already submitted
        if sequence and sequence not in submitted_sequences:
            should_auto_submit = True
            # Mark as submitted to prevent duplicates
            submitted_sequences.add(sequence)
            st.session_state[CHATBOT_TX_SUBMITTED_KEY] = submitted_sequences
            
            import logging
            logger = logging.getLogger("arc.mcp.tools")
            logger.info(f"Transaction {sequence} marked for auto-submit (first time)")
        else:
            import logging
            logger = logging.getLogger("arc.mcp.tools")
            logger.info(f"Transaction {sequence} already submitted, skipping auto-submit")
    
    # Build wallet component args
    # Use a unique key when there's a command to ensure React re-renders and executes it
    component_key = "chatbot_wallet_connector"
    if pending_action and pending_action.get("command") == "switch_network" and pending_action.get("sequence"):
        component_key = f"chatbot_wallet_connector_{pending_action.get('sequence')}"
    
    # Determine the expected chain ID based on the operation
    # If we're doing a Polygon mint (CCTP), expect Polygon chain
    expected_chain = chain_id_wallet
    if pending_action:
        # Check if this is a Polygon operation (CCTP mint)
        tx_label_lower = (tx_label or "").lower()
        # Check the tx_request for chainId hint
        tx_chain_id = None
        if tx_req and isinstance(tx_req, dict):
            # Check if chainId is specified in the transaction request
            tx_chain_id = tx_req.get("chainId")
            if tx_chain_id:
                if isinstance(tx_chain_id, str) and tx_chain_id.startswith("0x"):
                    tx_chain_id = int(tx_chain_id, 16)
                elif isinstance(tx_chain_id, str):
                    tx_chain_id = int(tx_chain_id)
        
        if tx_chain_id:
            expected_chain = tx_chain_id
        elif "polygon" in tx_label_lower or "mint" in tx_label_lower or "cctp" in tx_label_lower:
            expected_chain = 80002  # Polygon Amoy
        # Also check if we're switching to Polygon
        elif pending_action.get("command") == "switch_network":
            target = pending_action.get("targetChainId")
            if target:
                expected_chain = target
    
    wallet_args = {
        "key": component_key,
        "require_chain_id": expected_chain,
        "tx_request": tx_req,
        "action": action_hint,
        "tx_label": tx_label,
        "autoconnect": True,
        "auto_submit": should_auto_submit,  # Only auto-submit once
    }
    
    # Add network switch command if needed (without headless mode for proper user interaction)
    if pending_action and pending_action.get("command") == "switch_network" and pending_action.get("needs_wallet_command"):
        target_chain = pending_action.get("targetChainId")
        sequence = pending_action.get("sequence")
        wallet_args.update({
            # Remove headless mode - network switch needs interactive UI for MetaMask popup
            "command": "switch_network",
            "command_sequence": sequence,
            "command_payload": {"require_chain_id": target_chain},
        })
        # Update the required chain for the switch
        wallet_args["require_chain_id"] = target_chain
        expected_chain = target_chain  # Update expected chain for UI display
        
        # Debug logging
        import logging
        logger = logging.getLogger("arc.mcp.tools")
        logger.info(f"Sending network switch command to wallet component (interactive mode): target_chain={target_chain}, sequence={sequence}")
    
    # Debug: Log wallet args when there's a network switch command
    if pending_action and pending_action.get("command") == "switch_network":
        import logging
        logger = logging.getLogger("arc.mcp.tools")
        logger.info(f"Wallet args for network switch: {wallet_args}")
        st.info(f"🔍 Sending network switch to wallet component with key: {component_key}")
    
    # Debug: Show expected chain for transactions
    if tx_req and expected_chain:
        import logging
        logger = logging.getLogger("arc.mcp.tools")
        logger.info(f"Transaction expects chain {expected_chain} (hex: {hex(expected_chain)}), label: {tx_label}")
        if tx_req and isinstance(tx_req, dict):
            logger.info(f"Transaction chainId in request: {tx_req.get('chainId')}")
    
    # Always render the wallet component (now with command if needed)
    wallet_info = connect_wallet(**wallet_args)
    
    # Debug what the wallet component returns
    if wallet_info:
        st.info(f"ℹ️ MetaMask payload received: {wallet_info}")
    
    # Don't clear the needs_wallet_command flag immediately - wait for command completion
    # This ensures the command persists across Streamlit reruns until executed
    
    headless_payload = wallet_info if pending_action else None
    
    if headless_payload:
        import logging
        logger = logging.getLogger("arc.mcp.tools")
        logger.info(f"Wallet component returned from headless mode: {headless_payload}")
        
    # Debug info for network switch
    if pending_action and pending_action.get("command") == "switch_network":
        target_chain = pending_action.get("targetChainId")
        if target_chain == 80002:
            st.info(f"⚠️ Switching to Polygon network...")
        else:
            st.info(f"⚠️ Switching to ARC network...")
    
    # Always show manual network switch options for convenience
    if wallet_info and wallet_info.get("chainId"):
        current_chain = _normalise_chain_id(wallet_info.get("chainId"))
        with st.expander("🔄 Quick Network Switch", expanded=False):
            st.write(f"Current network: {'ARC' if current_chain == 5042002 else 'Polygon' if current_chain == 80002 else 'Unknown'}")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Switch to ARC", key="quick_switch_arc", disabled=(current_chain == 5042002)):
                    st.components.v1.html("""
                    <script>
                    if (window.ethereum) {
                        window.ethereum.request({
                            method: 'wallet_switchEthereumChain',
                            params: [{ chainId: '0x4cef52' }]
                        }).then(() => {
                            window.parent.location.reload();
                        }).catch((error) => {
                            if (error.code === 4902) {
                                window.ethereum.request({
                                    method: 'wallet_addEthereumChain',
                                    params: [{
                                        chainId: '0x4cef52',
                                        chainName: 'Arc Testnet',
                                        nativeCurrency: {
                                            name: 'USDC',
                                            symbol: 'USDC',
                                            decimals: 6
                                        },
                                        rpcUrls: ['https://rpc.testnet.arc.network'],
                                        blockExplorerUrls: ['https://testnet.arcscan.app']
                                    }]
                                }).then(() => {
                                    window.parent.location.reload();
                                });
                            }
                        });
                    }
                    </script>
                    """, height=0)
            with col2:
                if st.button("Switch to Polygon", key="quick_switch_polygon", disabled=(current_chain == 80002)):
                    st.components.v1.html("""
                    <script>
                    if (window.ethereum) {
                        window.ethereum.request({
                            method: 'wallet_switchEthereumChain',
                            params: [{ chainId: '0x13882' }]
                        }).then(() => {
                            window.parent.location.reload();
                        }).catch((error) => {
                            if (error.code === 4902) {
                                window.ethereum.request({
                                    method: 'wallet_addEthereumChain',
                                    params: [{
                                        chainId: '0x13882',
                                        chainName: 'Polygon Amoy Testnet',
                                        nativeCurrency: {
                                            name: 'MATIC',
                                            symbol: 'MATIC',
                                            decimals: 18
                                        },
                                        rpcUrls: ['https://rpc-amoy.polygon.technology'],
                                        blockExplorerUrls: ['https://amoy.polygonscan.com']
                                    }]
                                }).then(() => {
                                    window.parent.location.reload();
                                });
                            }
                        });
                    }
                    </script>
                    """, height=0)
    
    def _receipt_field(receipt: Any, field: str) -> Any:
        if isinstance(receipt, dict):
            return receipt.get(field)
        return getattr(receipt, field, None)

    # Update session state from wallet
    def _process_wallet_payload(payload: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None
        address = payload.get("address")
        if address:
            cached = st.session_state.get(DEFAULT_SESSION_KEY)
            merged = {**cached, **payload} if isinstance(cached, dict) else payload
            st.session_state[DEFAULT_SESSION_KEY] = merged
        tx_hash = payload.get("txHash")
        error = payload.get("error")
        status = str(payload.get("status") or "").lower()
        pending_snapshot = st.session_state.get(CHATBOT_PENDING_COMMAND_KEY)
        if pending_snapshot and error:
            st.session_state[CHATBOT_WALLET_RESULT_KEY] = payload
            st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
            st.session_state.pop(CHATBOT_WALLET_DEBUG_KEY, None)
            st.session_state.pop("chatbot_waiting_for_wallet", None)
            st.session_state.pop(CHATBOT_TX_SUBMITTED_KEY, None)
            st.session_state[CHATBOT_RESUME_PENDING_KEY] = True
            return payload
        if pending_snapshot and tx_hash and status not in {"error"}:
            st.session_state[CHATBOT_PENDING_TX_KEY] = {
                "txHash": tx_hash,
                "chainId": payload.get("chainId"),
                "hint": pending_snapshot.get("label") or pending_snapshot.get("hint"),
                "submitted_at": time.time(),
            }
            st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
            st.session_state.pop(CHATBOT_WALLET_DEBUG_KEY, None)
            st.session_state["chatbot_waiting_for_wallet"] = True
            return None
        if pending_snapshot and status in {"sent"}:
            st.session_state[CHATBOT_WALLET_RESULT_KEY] = payload
            st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
            st.session_state.pop(CHATBOT_WALLET_DEBUG_KEY, None)
            st.session_state.pop("chatbot_waiting_for_wallet", None)
            st.session_state[CHATBOT_RESUME_PENDING_KEY] = True
            return payload
        # Handle network switch completion
        if pending_snapshot and pending_snapshot.get("command") == "switch_network":
            # Check if switch was successful by comparing current chain with target
            if status == "switched":
                # Explicit success from frontend
                st.session_state[CHATBOT_WALLET_RESULT_KEY] = payload
                # Clear the pending action including needs_wallet_command flag
                st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
                st.session_state.pop(CHATBOT_WALLET_DEBUG_KEY, None)
                st.session_state.pop("chatbot_waiting_for_wallet", None)
                st.session_state.pop(CHATBOT_NETWORK_SWITCH_POLLS_KEY, None)
                st.session_state[CHATBOT_RESUME_PENDING_KEY] = True
                return payload
            elif payload.get("chainId"):
                # Check if chain ID matches target
                current_chain = _normalise_chain_id(payload.get("chainId"))
                target_chain = pending_snapshot.get("targetChainId")
                if current_chain and target_chain and current_chain == target_chain:
                    # Network switched successfully!
                    import logging
                    logger = logging.getLogger("arc.mcp.tools")
                    logger.info(f"Network switch detected: current chain {current_chain} matches target {target_chain}")
                    payload["network_switched"] = True
                    st.session_state[CHATBOT_WALLET_RESULT_KEY] = payload
                    # Clear the pending action including needs_wallet_command flag
                    st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
                    st.session_state.pop(CHATBOT_WALLET_DEBUG_KEY, None)
                    st.session_state.pop("chatbot_waiting_for_wallet", None)
                    st.session_state.pop(CHATBOT_NETWORK_SWITCH_POLLS_KEY, None)
                    st.session_state[CHATBOT_RESUME_PENDING_KEY] = True
                    return payload
        return None

    result_payload: Optional[Dict[str, Any]] = None
    for candidate in (headless_payload, wallet_info):
        if candidate is not None:
            debug_state = st.session_state.get(CHATBOT_WALLET_DEBUG_KEY, {})
            debug_state["last_component_payload"] = candidate
            st.session_state[CHATBOT_WALLET_DEBUG_KEY] = debug_state
        processed = _process_wallet_payload(candidate)
        if processed:
            result_payload = processed
            if candidate is headless_payload:
                debug_state = st.session_state.get(CHATBOT_WALLET_DEBUG_KEY, {})
                debug_state["last_headless_result"] = processed
                st.session_state[CHATBOT_WALLET_DEBUG_KEY] = debug_state

    if result_payload:
        tx_hash = result_payload.get("txHash")
        error = result_payload.get("error")
        status = str(result_payload.get("status") or "").lower()
        token_source = tx_hash or error or status
        already_shown = st.session_state.get(CHATBOT_WALLET_CONFIRMATION_KEY)
        if token_source and already_shown != token_source:
            st.session_state[CHATBOT_WALLET_CONFIRMATION_KEY] = token_source
            st.session_state.pop(CHATBOT_HEADLESS_LOCK_KEY, None)
            if tx_hash:
                explorer = result_payload.get("explorer") or result_payload.get("explorerUrl")
                message = f"Transaction submitted: `{tx_hash}`."
                if explorer:
                    message += f" [View on explorer]({explorer})"
                st.success(message)
            elif error:
                st.error(f"MetaMask reported an error: {error}")
            elif status == "sent":
                st.info("MetaMask reports the transaction was sent.")

    pending_tx_state = st.session_state.get(CHATBOT_PENDING_TX_KEY)
    if pending_tx_state and w3 is not None:
        tx_hash = pending_tx_state.get("txHash")
        receipt = None
        try:
            if tx_hash:
                receipt = w3.eth.get_transaction_receipt(tx_hash)
        except TransactionNotFound:
            receipt = None
        except Exception as exc:
            st.warning(f"Unable to fetch confirmation for transaction `{tx_hash}`: {exc}")
        if receipt is not None:
            status_value = _receipt_field(receipt, "status")
            outcome = "confirmed" if status_value in (1, True) else "failed"
            receipt_payload = {
                "transactionHash": _receipt_field(receipt, "transactionHash"),
                "status": status_value,
                "blockNumber": _receipt_field(receipt, "blockNumber"),
                "gasUsed": _receipt_field(receipt, "gasUsed"),
            }
            if isinstance(receipt_payload["transactionHash"], bytes):
                receipt_payload["transactionHash"] = receipt_payload["transactionHash"].hex()
            if receipt_payload["transactionHash"] is None and tx_hash:
                receipt_payload["transactionHash"] = tx_hash
            st.session_state[CHATBOT_WALLET_RESULT_KEY] = {
                "txHash": receipt_payload["transactionHash"],
                "receipt": receipt_payload,
                "status": outcome,
            }
            st.session_state.pop(CHATBOT_PENDING_TX_KEY, None)
            st.session_state.pop("chatbot_waiting_for_wallet", None)
            st.session_state.pop(CHATBOT_WALLET_DEBUG_KEY, None)
            st.session_state[CHATBOT_RESUME_PENDING_KEY] = True
            st.rerun()

    debug_state = st.session_state.get(CHATBOT_WALLET_DEBUG_KEY, {})
    pending_snapshot = pending_action if isinstance(pending_action, dict) else None
    pending_tx_state = st.session_state.get(CHATBOT_PENDING_TX_KEY)
    if tx_req or debug_state or pending_tx_state:
        with st.expander("Wallet automation status", expanded=False):
            if not debug_state and not pending_tx_state:
                st.write("No automation metadata yet.")
            else:
                st.write(f"**Command:** {debug_state.get('command') or 'unknown'}")
                st.write(f"**Sequence:** {debug_state.get('sequence', '—')}")
                if debug_state.get("headless_invoked") or (pending_action and pending_action.get("command") == "switch_network"):
                    st.success("Wallet command invoked; waiting for MetaMask popup.")
                else:
                    st.info("Preparing wallet command…")
                if wallet_info and wallet_info.get("address"):
                    st.write(f"**Connected wallet:** {wallet_info['address']}")
                else:
                    st.warning("Wallet not connected yet – connect MetaMask to continue.")
                if wallet_info and wallet_info.get("chainId") and expected_chain:
                    chain_matches = str(wallet_info["chainId"]).lower() == hex(expected_chain).lower()
                    if chain_matches:
                        st.success(f"Chain OK ({wallet_info['chainId']}).")
                    else:
                        expected_name = "Polygon" if expected_chain == 80002 else "Arc" if expected_chain == 5042002 else f"chain {expected_chain}"
                        st.warning(f"Please switch to {expected_name} network using the buttons below.")
                if pending_tx_state:
                    st.info(f"⏳ Waiting for on-chain confirmation of `{pending_tx_state.get('txHash', 'unknown')}`…")
                elif not debug_state.get("headless_invoked") and not (pending_action and pending_action.get("command") == "switch_network"):
                    st.write("Waiting for wallet command to execute. If this takes longer than a few seconds, click retry below.")
                payload = debug_state.get("last_component_payload")
                if payload:
                    st.write("**Latest wallet component payload:**")
                    st.json(payload)
                else:
                    st.write("Waiting for wallet component response…")
                if pending_snapshot and tx_req and not pending_tx_state:
                    if st.button("Retry MetaMask command", key="chatbot_retry_wallet_command"):
                        pending_snapshot = dict(pending_snapshot)
                        pending_snapshot["sequence"] = int(time.time() * 1000)
                        pending_snapshot.pop("headless_executed", None)
                        st.session_state.pop(CHATBOT_HEADLESS_LOCK_KEY, None)
                        st.session_state[CHATBOT_PENDING_COMMAND_KEY] = pending_snapshot
                        debug_state["retry_requested_at"] = time.time()
                        st.session_state[CHATBOT_WALLET_DEBUG_KEY] = debug_state
                        st.rerun()

    resume_pending = st.session_state.pop(CHATBOT_RESUME_PENDING_KEY, False)

    attachments = st.file_uploader(
        "Attach documents (txt, md, pdf, docx, csv, json)",
        type=["txt", "md", "pdf", "docx", "csv", "json"],
        accept_multiple_files=True,
        key="chatbot_attachments",
    )
    attachment_payloads: list[Dict[str, Any]] = []
    if attachments:
        for file in attachments:
            try:
                data = file.read()
            except Exception:
                data = b""
            size = len(data)
            try:
                file.seek(0)
            except Exception:
                pass
            filename = getattr(file, "name", "document")
            mime = getattr(file, "type", None) or mimetypes.guess_type(filename)[0]
            attachment_payloads.append(
                {
                    "name": filename,
                    "type": mime or "application/octet-stream",
                    "size": size,
                    "data": data,
                }
            )
    st.session_state["chatbot_attachment_payloads"] = attachment_payloads
    _update_combined_upload_payloads()
    include_attachments = st.checkbox(
        "Include attachments in next message",
        value=True,
        key="chatbot_include_attachments",
    )
    clip_len = int(os.getenv("CHATBOT_ATTACHMENT_MAX_CHARS", "6000"))

    client = create_azure_client()
    if client is None:
        st.info(
            "Set environment variables `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, and optionally `AZURE_OPENAI_API_VERSION` "
            "inside `.env` to enable the chatbot."
        )

    initialize_chat_state()

    render_llm_history(st.session_state.messages)

    prompt = st.chat_input(
        "Ask Doggo anything about setup, credit scoring, or MCP tooling…",
        key="chatbot_prompt",
    )
    wallet_blocked, wallet_block_reason = _wallet_flow_blocked()
    prompt_blocked = False

    if prompt:
        attachment_context = (
            build_attachment_context(attachments, clip_len) if (attachments and include_attachments) else ""
        )
        composed_prompt = (
            f"{prompt}\n\n[Attached documents]\n{attachment_context}" if attachment_context else prompt
        )

        append_message("user", composed_prompt)
        with st.chat_message("user"):
            st.markdown(prompt)
            if attachment_context:
                with st.expander("Attachments included in this turn"):
                    for f in attachments:
                        st.write(f"- {getattr(f, 'name', 'document')}")
        if wallet_blocked and wallet_block_reason != "manual_network":
            prompt_blocked = True

    if prompt_blocked:
        with st.chat_message("assistant"):
            col1, col2 = st.columns([3, 1])
            with col1:
                if wallet_block_reason == "manual_network":
                    target_network = (manual_request_state or {}).get("network_name") or "the requested"
                    target_chain = (manual_request_state or {}).get("target_chain")
                    chain_label = f" (chainId {target_chain})" if target_chain else ""
                    st.warning(
                        f"I'm paused until you switch MetaMask to {target_network}{chain_label} and let me know it's done. "
                        "Reply once you've switched so I can verify."
                    )
                else:
                    st.info("I'm still waiting for the previous wallet transaction to finish. Approve it in MetaMask or wait for confirmation.")
            with col2:
                if st.button("🔄 Clear & Retry", key="clear_wallet_block"):
                    # Clear all wallet-related blocking states
                    st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
                    st.session_state.pop(CHATBOT_PENDING_TX_KEY, None)
                    st.session_state.pop("chatbot_waiting_for_wallet", None)
                    st.session_state.pop(CHATBOT_TX_SUBMITTED_KEY, None)
                    st.session_state.pop(CHATBOT_MANUAL_NETWORK_REQUEST_KEY, None)
                    st.session_state.pop(CHATBOT_WALLET_DEBUG_KEY, None)
                    st.session_state.pop(CHATBOT_HEADLESS_LOCK_KEY, None)
                    import logging
                    logger = logging.getLogger("arc.mcp.tools")
                    logger.info("User manually cleared wallet blocking states")
                    st.rerun()
        prompt = None

    if client is None:
        if prompt:
            fallback = (
                "Azure OpenAI credentials are missing. Configure them to receive generated responses, or review the Intro page."
            )
            append_message("assistant", fallback)
            with st.chat_message("assistant"):
                st.markdown(fallback)
        return

    deployment = os.getenv(AZURE_DEPLOYMENT_ENV)
    if not deployment:
        warning = (
            "Environment variable `AZURE_OPENAI_CHAT_DEPLOYMENT` is not set. Add it to `.env` with your deployed "
            "Azure OpenAI model name."
        )
        if prompt:
            append_message("assistant", warning)
            with st.chat_message("assistant"):
                st.warning(warning)
        else:
            st.warning(warning)
        return

    rpc_url = os.getenv(ARC_RPC_ENV)
    private_key = os.getenv(PRIVATE_KEY_ENV)
    default_gas_limit = int(os.getenv(GAS_LIMIT_ENV, "200000"))
    gas_price_gwei = os.getenv(GAS_PRICE_GWEI_ENV, "1")

    w3 = get_web3_client(rpc_url)
    if w3 is None:
        info_msg = "Connect to the ARC RPC to unlock MCP tools in chat."
        if prompt:
            append_message("assistant", info_msg)
            with st.chat_message("assistant"):
                st.info(info_msg)
        else:
            st.info(info_msg)
        return

    try:
        chain_id = w3.eth.chain_id
    except Exception:
        chain_id = None

    roles_key = "role_addresses"
    stored_roles = st.session_state.get(roles_key)
    if not isinstance(stored_roles, dict):
        stored_roles = {"Owner": "", "Lender": "", "Borrower": ""}
    else:
        for role_name in ("Owner", "Lender", "Borrower"):
            stored_roles.setdefault(role_name, "")
    st.session_state[roles_key] = stored_roles
    role_addresses: Dict[str, str] = stored_roles

    owner_pk = os.getenv(PRIVATE_KEY_ENV)
    lender_pk = os.getenv("LENDER_PRIVATE_KEY")
    borrower_pk = os.getenv("BORROWER_PRIVATE_KEY")
    role_private_keys = {
        "Owner": owner_pk,
        "Lender": lender_pk,
        "Borrower": borrower_pk,
    }

    sbt_address = os.getenv(SBT_ADDRESS_ENV)
    sbt_abi_path = os.getenv(TRUSTMINT_SBT_ABI_PATH_ENV)
    sbt_tools_schema: list[Dict[str, Any]] = []
    sbt_function_map: Dict[str, Any] = {}
    sbt_error: str | None = None
    
    sbt_guard: Optional[Callable[[str], Optional[str]]] = None


    sbt_guard: Optional[Callable[[str], Optional[str]]] = None
    if sbt_address and sbt_abi_path:
        try:
            sbt_abi = load_contract_abi(sbt_abi_path)
            if not sbt_abi:
                sbt_error = f"ABI file loaded but contains no ABI data: {sbt_abi_path}"
            else:
                try:
                    sbt_contract = w3.eth.contract(address=Web3.to_checksum_address(sbt_address), abi=sbt_abi)
                    sbt_tools_schema, sbt_function_map = build_llm_toolkit(
                        w3=w3,
                        contract=sbt_contract,
                        token_decimals=0,
                        private_key=private_key,
                        default_gas_limit=default_gas_limit,
                        gas_price_gwei=gas_price_gwei,
                    )
                except ValueError as e:
                    sbt_error = f"Invalid SBT contract address: {e}"
                except Exception as e:
                    sbt_error = f"Failed to build SBT toolkit: {e}"
        except (FileNotFoundError, ValueError) as e:
            sbt_error = str(e)
        except Exception as e:
            sbt_error = f"Unexpected error loading SBT ABI: {e}"
            sbt_contract = w3.eth.contract(address=Web3.to_checksum_address(sbt_address), abi=sbt_abi)
            sbt_tools_schema, sbt_function_map = build_llm_toolkit(
                w3=w3,
                contract=sbt_contract,
                token_decimals=0,
                private_key=private_key,
                default_gas_limit=default_gas_limit,
                gas_price_gwei=gas_price_gwei,
            )
            sbt_guard = build_sbt_guard(w3, sbt_contract)
        except Exception:
            pass
    if sbt_function_map.get("issueScore"):
        sbt_function_map["issueScore"] = _guard_issue_score(sbt_function_map["issueScore"])
    pool_address = os.getenv(LENDING_POOL_ADDRESS_ENV)
    pool_abi_path = os.getenv(LENDING_POOL_ABI_PATH_ENV)
    usdc_address = os.getenv(USDC_ADDRESS_ENV)
    usdc_abi_path = os.getenv(USDC_ABI_PATH_ENV)
    usdc_decimals = int(os.getenv(USDC_DECIMALS_ENV, "6"))
    pool_tools_schema: list[Dict[str, Any]] = []
    pool_function_map: Dict[str, Any] = {}
    pool_error: str | None = None
    
    if pool_address and pool_abi_path:
        try:
            pool_abi = load_contract_abi(pool_abi_path)
            if not pool_abi:
                pool_error = f"ABI file loaded but contains no ABI data: {pool_abi_path}"
            else:
                usdc_abi = load_contract_abi(usdc_abi_path) if usdc_abi_path else None
                try:
                    pool_contract = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=pool_abi)
                    pool_tools_schema, pool_function_map = build_lending_pool_toolkit(
                        w3=w3,
                        pool_contract=pool_contract,
                        token_decimals=usdc_decimals,
                        native_decimals=18,
                        private_key=private_key,
                        default_gas_limit=default_gas_limit,
                        gas_price_gwei=gas_price_gwei,
                        role_addresses=role_addresses,
                        role_private_keys=role_private_keys,
                        borrower_guard=sbt_guard,
                    )
                except ValueError as e:
                    pool_error = f"Invalid LendingPool contract address: {e}"
                except Exception as e:
                    pool_error = f"Failed to build LendingPool toolkit: {e}"
        except (FileNotFoundError, ValueError) as e:
            pool_error = str(e)
        except Exception as e:
            pool_error = f"Unexpected error loading LendingPool ABI: {e}"
            pool_contract = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=pool_abi)
            pool_tools_schema, pool_function_map = build_lending_pool_toolkit(
                w3=w3,
                pool_contract=pool_contract,
                token_decimals=usdc_decimals,
                native_decimals=18,
                private_key=private_key,
                default_gas_limit=default_gas_limit,
                gas_price_gwei=gas_price_gwei,
                role_addresses=role_addresses,
                role_private_keys=role_private_keys,
                borrower_guard=sbt_guard,
            )
        except Exception:
            pass
    bridge_tools_schema, bridge_function_map = build_bridge_toolkit()
    borrower_bridge_tools_schema, borrower_bridge_function_map = build_borrower_bridge_toolkit()
    state_tools_schema, state_function_map = _build_chatbot_state_tools(chain_id, roles_key, role_addresses)

    tools_schema = sbt_tools_schema + pool_tools_schema + bridge_tools_schema + borrower_bridge_tools_schema + state_tools_schema
    function_map: Dict[str, Callable[..., str]] = {}
    function_map.update(sbt_function_map)
    function_map.update(pool_function_map)
    function_map.update(bridge_function_map)
    function_map.update(borrower_bridge_function_map)
    function_map.update(state_function_map)

    if not tools_schema:
        missing_config = []
        if not sbt_address:
            missing_config.append(f"`{SBT_ADDRESS_ENV}`")
        if not sbt_abi_path:
            missing_config.append(f"`{TRUSTMINT_SBT_ABI_PATH_ENV}`")
        if not pool_address:
            missing_config.append(f"`{LENDING_POOL_ADDRESS_ENV}`")
        if not pool_abi_path:
            missing_config.append(f"`{LENDING_POOL_ABI_PATH_ENV}`")
        
        error_details = []
        if sbt_error:
            error_details.append(f"**SBT Tools Error:** {sbt_error}")
        if pool_error:
            error_details.append(f"**LendingPool Tools Error:** {pool_error}")
        
        with st.container():
            st.warning("**No MCP tools are available for the current contract configuration.**")
            
            if missing_config:
                st.markdown("### Missing Environment Variables")
                st.markdown("Set the following in your `.env` file at the repository root:")
                for var in missing_config:
                    st.code(f"{var}=your_value_here", language="bash")
                
                # Check if ABI path is missing and provide compilation instructions
                needs_abi = any(TRUSTMINT_SBT_ABI_PATH_ENV in var or LENDING_POOL_ABI_PATH_ENV in var for var in missing_config)
                if needs_abi:
                    st.markdown("**Note:** ABI files need to be generated by compiling your contracts.")
                    st.markdown("**Install Foundry (if not installed):**")
                    st.code("curl -L https://foundry.paradigm.xyz | bash\nfoundryup", language="bash")
                    st.markdown("**Compile contracts:**")
                    st.code("cd blockchain_code && forge build", language="bash")
                    st.markdown("This will generate ABI files in `blockchain_code/out/` directory.")
                    st.info("📖 See `SETUP_MCP.md` for detailed setup instructions.")
                
                st.markdown("**Example `.env` configuration:**")
                st.code(f"""# TrustMint SBT Contract
{SBT_ADDRESS_ENV}=0xYourSBTContractAddress
{TRUSTMINT_SBT_ABI_PATH_ENV}=blockchain_code/out/TrustMintSBT.sol/TrustMintSBT.json

# LendingPool Contract (optional)
{LENDING_POOL_ADDRESS_ENV}=0xYourLendingPoolAddress
{LENDING_POOL_ABI_PATH_ENV}=blockchain_code/out/LendingPool.sol/LendingPool.json
{USDC_ADDRESS_ENV}=0xYourUSDCAddress
{USDC_ABI_PATH_ENV}=blockchain_code/out/USDC.sol/USDC.json

# RPC Configuration
{ARC_RPC_ENV}=https://your-arc-rpc-url
{PRIVATE_KEY_ENV}=0xYourPrivateKey""", language="bash")
            
            if error_details:
                st.markdown("### Configuration Errors")
                for detail in error_details:
                    st.error(detail)
                    # Provide specific help for FileNotFoundError
                    if "not found" in detail.lower() or "file not found" in detail.lower():
                        st.info("💡 **Tip:** Run `cd blockchain_code && forge build` to generate ABI files. See `SETUP_MCP.md` for full instructions.")
            
            if not missing_config and not error_details:
                st.info("Contract addresses and ABI paths are set, but no tools were generated. Check that the ABI files exist and contain valid contract ABIs.")
                st.markdown("**Verify:**")
                st.code("""# Check if ABI files exist
ls -la blockchain_code/out/TrustMintSBT.sol/TrustMintSBT.json
ls -la blockchain_code/out/LendingPool.sol/LendingPool.json

# If missing, compile contracts
cd blockchain_code && forge build""", language="bash")
        
        return

    resume_mode = bool(resume_pending and not prompt)

    _cleanup_pending_tool_calls()
    blocked, block_reason = _wallet_flow_blocked()
    if blocked and block_reason != "manual_network":
        return

    if not prompt and not resume_mode:
        return

    waves = load_lottie_json(WAVES_PATH)
    spinner_text = (
        "Resuming wallet-dependent workflow…"
        if resume_mode and not prompt
        else "GPT 5 is orchestrating MCP tools…"
    )

    def _run_conversation_with_status(callback: Callable[[Optional[str]], None]) -> None:
        run_mcp_llm_conversation(
            client,
            deployment,
            st.session_state.messages,
            tools_schema,
            function_map,
            wallet_widget_callback=None,
            status_callback=callback,
        )

    with st.chat_message("assistant"):
        gif_display = TaskGifDisplay()
        initial_hint = _derive_task_hint_from_state(
            pending_action,
            st.session_state.get(CHATBOT_PENDING_TX_KEY),
        )
        gif_display.set_context(initial_hint)
        gif_display.begin_conversation()

        if waves:
            from streamlit_lottie import st_lottie_spinner

            with st_lottie_spinner(waves, key="waves_spinner"):
                _run_conversation_with_status(gif_display.handle_status_event)
        else:
            with st.spinner(spinner_text):
                _run_conversation_with_status(gif_display.handle_status_event)

        gif_display.end_conversation()
    
    # If a transaction was prepared during the conversation, rerun to show it
    if st.session_state.get("chatbot_needs_tx_rerun"):
        st.session_state.pop("chatbot_needs_tx_rerun", None)
        import logging
        logging.getLogger("arc.mcp.tools").info("Auto-rerunning to display pending transaction in wallet widget...")
        st.rerun()

    if st.session_state.pop("chatbot_roles_dirty", False):
        import logging
        logging.getLogger("arc.mcp.tools").info("Auto-rerunning to rebuild toolkits with updated role assignments...")
        st.rerun()

