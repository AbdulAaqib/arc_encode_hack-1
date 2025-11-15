from __future__ import annotations

import os
from time import time
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
from ..web3_utils import get_web3_client, load_contract_abi
from ..wallet_connect_component import wallet_command, connect_wallet
from ..session import DEFAULT_SESSION_KEY
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


def _wallet_flow_blocked() -> bool:
    return bool(
        st.session_state.get(CHATBOT_PENDING_COMMAND_KEY)
        or st.session_state.get(CHATBOT_PENDING_TX_KEY)
        or st.session_state.get("chatbot_waiting_for_wallet")
    )


def _cleanup_pending_tool_calls() -> None:
    messages = st.session_state.get("messages")
    if not isinstance(messages, list) or not messages:
        return
    
    while messages:
        last = messages[-1]
        if not isinstance(last, dict):
            break
        if last.get("role") != "assistant":
            break
        tool_calls = last.get("tool_calls")
        if not tool_calls:
            break
        messages.pop()


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
        if choice is None:
            return tool_error("Chain preference must be either 'ARC' or 'POLYGON'.")
        st.session_state[CHAIN_PREF_SESSION_KEY] = choice
        return tool_success({"chain_preference": choice})

    def list_chains_tool() -> str:
        return tool_success({"availableChains": ["ARC", "POLYGON"]})

    def get_wallet_tool() -> str:
        """Get wallet state from session - user must connect via UI."""
        # Check if there's a completed transaction
        result = st.session_state.get(CHATBOT_WALLET_RESULT_KEY)
        if result and isinstance(result, dict):
            if result.get("txHash"):
                # Clear the result after reading
                st.session_state.pop(CHATBOT_WALLET_RESULT_KEY, None)
                st.session_state.pop(CHATBOT_PENDING_COMMAND_KEY, None)
                cached = _cached_wallet_state()
                return tool_success({
                    "wallet": cached,
                    "transaction": {
                        "txHash": result["txHash"],
                        "status": "confirmed"
                    }
                })
        
        # Check if there's a pending transaction
        pending = st.session_state.get(CHATBOT_PENDING_COMMAND_KEY)
        if pending and isinstance(pending, dict):
            if pending.get("command") == "send_transaction":
                cached = _cached_wallet_state()
                return tool_success({
                    "wallet": cached,
                    "pending": True,
                    "message": (
                        "Transaction is being sent to MetaMask. Waiting for user approval. "
                        "Keep polling this tool to detect when the transaction is confirmed."
                    )
                })
        
        cached = _cached_wallet_state()
        if cached and cached.get("address"):
            return tool_success({"wallet": cached})
        return tool_success({"wallet": None})

    def connect_wallet_tool() -> str:
        """Trigger MetaMask connection - user just needs to approve the popup."""
        # Check if already connected
        cached = _cached_wallet_state()
        if cached and cached.get("address"):
            return tool_success({"wallet": cached, "message": "Wallet already connected."})
        
        # Set pending flag - widget will trigger MetaMask on next render
        sequence = int(time() * 1000)
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

    def switch_network_tool() -> str:
        """Request network switch - user approves via wallet widget."""
        if expected_chain_id is None:
            return tool_error("Expected chain id is not configured.")
        
        return tool_success({
            "message": (
                "Please use the wallet widget at the top of the page to switch to the ARC network. "
                "Click the 'Switch Network' button if it appears."
            ),
            "targetChainId": expected_chain_id
        })

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

    register(
        "getLoanChainPreference",
        "Return the current loan settlement chain preference.",
        {"type": "object", "properties": {}, "required": []},
        lambda: get_pref_tool(),
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
        "Request MetaMask to switch to the ARC network.",
        {"type": "object", "properties": {}, "required": []},
        lambda: switch_network_tool(),
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

    st.title("💬 PawChain Chatbot")
    st.caption(
        "Powered by OpenAI GPT-5 and MCP tools. Connect your wallet below, then chat with Doggo for agentic assistance."
    )
    
    # Always-visible wallet widget for agentic MetaMask interactions
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
        if action_type == "send_transaction":
            tx_req = pending_action.get("tx_request")
            action_hint = "eth_sendTransaction"
            tx_label = pending_action.get("label", "Confirm Transaction")
            st.info("🔄 Sending transaction to MetaMask... Approve the popup to continue.")
            lock_sequence = st.session_state.get(CHATBOT_HEADLESS_LOCK_KEY)
            sequence = pending_action.get("sequence")
            if sequence is None:
                sequence = int(time() * 1000)
                pending_action["sequence"] = sequence
                st.session_state[CHATBOT_PENDING_COMMAND_KEY] = pending_action
            
            debug_state = st.session_state.get(CHATBOT_WALLET_DEBUG_KEY, {})
            last_invoked_at = debug_state.get("headless_invoked_at")
            should_retry = False
            if (
                debug_state.get("headless_invoked")
                and isinstance(last_invoked_at, (int, float))
                and (time() - float(last_invoked_at)) > 6
            ):
                should_retry = True
                st.session_state.pop(CHATBOT_HEADLESS_LOCK_KEY, None)
                pending_action.pop("headless_executed", None)
                lock_sequence = None
                debug_state["headless_invoked"] = False
                debug_state["headless_invoked_at"] = None
                debug_state["auto_retry_at"] = time()

            debug_state.update(
                {
                    "command": action_type,
                    "sequence": sequence,
                    "tx_hint": tx_label,
                    "tx_value": tx_req.get("value") if isinstance(tx_req, dict) else None,
                    "tx_to": tx_req.get("to") if isinstance(tx_req, dict) else None,
                    "invoked_at": time(),
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
                debug_state["headless_invoked_at"] = time()
                st.session_state[CHATBOT_WALLET_DEBUG_KEY] = debug_state

    wallet_info = connect_wallet(
        key="chatbot_wallet_connector",
        require_chain_id=chain_id_wallet,
        tx_request=tx_req,
        action=action_hint,
        tx_label=tx_label,
        autoconnect=True,
        auto_submit=bool(tx_req),
    )
    
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
            st.session_state[CHATBOT_RESUME_PENDING_KEY] = True
            return payload
        if pending_snapshot and tx_hash and status not in {"error"}:
            st.session_state[CHATBOT_PENDING_TX_KEY] = {
                "txHash": tx_hash,
                "chainId": payload.get("chainId"),
                "hint": pending_snapshot.get("label") or pending_snapshot.get("hint"),
                "submitted_at": time(),
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
        with st.expander("Wallet automation status", expanded=True):
            if not debug_state and not pending_tx_state:
                st.write("No automation metadata yet.")
            else:
                st.write(f"**Command:** {debug_state.get('command') or 'unknown'}")
                st.write(f"**Sequence:** {debug_state.get('sequence', '—')}")
                if debug_state.get("headless_invoked"):
                    st.success("Headless wallet command invoked; waiting for MetaMask.")
                else:
                    st.info("Preparing headless wallet command…")
                if wallet_info and wallet_info.get("address"):
                    st.write(f"**Connected wallet:** {wallet_info['address']}")
                else:
                    st.warning("Wallet not connected yet – connect MetaMask to continue.")
                if wallet_info and wallet_info.get("chainId") and chain_id_wallet:
                    chain_matches = str(wallet_info["chainId"]).lower() == hex(chain_id_wallet).lower()
                    if chain_matches:
                        st.success(f"Chain OK ({wallet_info['chainId']}).")
                    else:
                        st.warning(f"Switch wallet to chain id {chain_id_wallet} before MetaMask can submit.")
                if pending_tx_state:
                    st.info(f"⏳ Waiting for on-chain confirmation of `{pending_tx_state.get('txHash', 'unknown')}`…")
                elif not debug_state.get("headless_invoked"):
                    st.write("Waiting for headless wallet command to run. If this takes longer than a few seconds, click retry below.")
                payload = debug_state.get("last_component_payload")
                if payload:
                    st.write("**Latest wallet component payload:**")
                    st.json(payload)
                else:
                    st.write("Waiting for wallet component response…")
                if pending_snapshot and tx_req and not pending_tx_state:
                    if st.button("Retry MetaMask command", key="chatbot_retry_wallet_command"):
                        pending_snapshot = dict(pending_snapshot)
                        pending_snapshot["sequence"] = int(time() * 1000)
                        pending_snapshot.pop("headless_executed", None)
                        st.session_state.pop(CHATBOT_HEADLESS_LOCK_KEY, None)
                        st.session_state[CHATBOT_PENDING_COMMAND_KEY] = pending_snapshot
                        debug_state["retry_requested_at"] = time()
                        st.session_state[CHATBOT_WALLET_DEBUG_KEY] = debug_state
                        st.rerun()

    resume_pending = st.session_state.pop(CHATBOT_RESUME_PENDING_KEY, False)

    attachments = st.file_uploader(
        "Attach documents (txt, md, pdf, docx, csv, json)",
        type=["txt", "md", "pdf", "docx", "csv", "json"],
        accept_multiple_files=True,
        key="chatbot_attachments",
    )
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
    wallet_blocked = _wallet_flow_blocked()
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
        if wallet_blocked:
            prompt_blocked = True

    if prompt_blocked:
        with st.chat_message("assistant"):
            st.info("I'm still waiting for the previous wallet transaction to finish. Approve it in MetaMask or wait for confirmation.")
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
    state_tools_schema, state_function_map = _build_chatbot_state_tools(chain_id, roles_key, role_addresses)

    bridge_tools_schema, bridge_function_map = build_bridge_toolkit()
    state_tools_schema, state_function_map = _build_chatbot_state_tools(chain_id, roles_key, role_addresses)

    tools_schema = sbt_tools_schema + pool_tools_schema + bridge_tools_schema + state_tools_schema
    function_map: Dict[str, Callable[..., str]] = {}
    function_map.update(sbt_function_map)
    function_map.update(pool_function_map)
    function_map.update(bridge_function_map)
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
    if _wallet_flow_blocked():
        return

    if not prompt and not resume_mode:
        return

    waves = load_lottie_json(WAVES_PATH)
    spinner_text = "Resuming wallet-dependent workflow…" if resume_mode and not prompt else "GPT 5 is orchestrating MCP tools…"

    if waves:
        from streamlit_lottie import st_lottie_spinner

        with st.chat_message("assistant"):
            with st_lottie_spinner(waves, key="waves_spinner"):
                run_mcp_llm_conversation(
                    client, deployment, st.session_state.messages, tools_schema, function_map,
                    wallet_widget_callback=None
                )
    else:
        with st.spinner(spinner_text):
            run_mcp_llm_conversation(
                client, deployment, st.session_state.messages, tools_schema, function_map,
                wallet_widget_callback=None
            )
    
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

