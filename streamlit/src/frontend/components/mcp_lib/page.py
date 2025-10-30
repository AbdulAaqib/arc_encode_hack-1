from __future__ import annotations

import os
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import streamlit as st
from web3 import Web3

from ..config import (
    ARC_RPC_ENV,
    SBT_ADDRESS_ENV,
    TRUSTMINT_SBT_ABI_PATH_ENV,
    PRIVATE_KEY_ENV,
    GAS_LIMIT_ENV,
    GAS_PRICE_GWEI_ENV,
    USDC_DECIMALS_ENV,
    LENDING_POOL_ADDRESS_ENV,
    LENDING_POOL_ABI_PATH_ENV,
    USDC_ADDRESS_ENV,
    USDC_ABI_PATH_ENV,
    BRIDGE_PRIVATE_KEY_ENV,
    POLYGON_RPC_ENV,
    POLYGON_PRIVATE_KEY_ENV,
    get_sbt_address,
)
from ..cctp_bridge import (
    POLYGON_AMOY_CHAIN_ID,
    BridgeError,
    guess_default_lending_pool_abi_path,
    initiate_arc_to_polygon_bridge,
    polygon_explorer_url,
    resume_arc_to_polygon_bridge,
    transfer_arc_usdc,
)
from ..toolkit import build_llm_toolkit, build_lending_pool_toolkit
from ..wallet_connect_component import connect_wallet, wallet_command
from ..web3_utils import get_web3_client, load_contract_abi
from .tool_runner import render_tool_runner

LOGGER_NAME = "arc.mcp_polygon"
polygon_logger = logging.getLogger(LOGGER_NAME)
if not polygon_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    polygon_logger.addHandler(handler)
polygon_logger.setLevel(logging.INFO)
polygon_logger.propagate = False

_SBT_TOOL_ROLES = {
    "hasSbt": "Read-only",
    "getScore": "Read-only",
    "issueScore": "Owner",
    "revokeScore": "Owner",
}

_POOL_TOOL_ROLES = {
    "availableLiquidity": "Read-only",
    "lenderBalance": "Read-only",
    "getLoan": "Read-only",
    "isBanned": "Read-only",
    "deposit": "Lender",
    "withdraw": "Lender",
    "openLoan": "Owner",
    "repay": "Borrower",
    "checkDefaultAndBan": "Owner",
    "unban": "Owner",
}

MCP_BRIDGE_SESSION_KEY = "mcp_cctp_bridge_state"
MCP_ARC_TRANSFER_SESSION_KEY = "mcp_arc_transfer_state"

MCP_POLYGON_COMMAND_KEY = "mcp_polygon_wallet_command"
MCP_POLYGON_COMMAND_SEQ_KEY = "mcp_polygon_wallet_command_seq"
MCP_POLYGON_COMMAND_ARGS_KEY = "mcp_polygon_wallet_command_args"
MCP_POLYGON_LOGS_KEY = "mcp_polygon_wallet_logs"
MCP_POLYGON_PENDING_TX_KEY = "mcp_polygon_pending_tx_request"
MCP_POLYGON_WALLET_STATE_KEY = "mcp_polygon_wallet_state"
MCP_POLYGON_AUTO_SWITCH_KEY = "mcp_polygon_auto_switch_attempted"
MCP_POLYGON_STATUS_KEY = "mcp_polygon_status_message"
MCP_POLYGON_COMPLETE_KEY = "mcp_polygon_completion_state"

ATTESTATION_POLL_INTERVAL = 5
ATTESTATION_TIMEOUT = 600
ATTESTATION_INITIAL_TIMEOUT = 30


def _resolve_polygon_address(role_addresses: Dict[str, str], connected_address: Optional[str]) -> Optional[str]:
    if connected_address:
        return connected_address
    for role in ("Borrower", "Lender", "Owner"):
        candidate = role_addresses.get(role)
        if candidate:
            return candidate
    return None


def _normalise_chain_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped, 0)
        except ValueError:
            try:
                return int(stripped)
            except ValueError:
                return None
    return None


def _log_polygon_event(message: str, logs: list[str], *, level: str = "info") -> list[str]:
    text = str(message)
    log_method = getattr(polygon_logger, level, polygon_logger.info)
    log_method(text)
    updated = logs + [text]
    st.session_state[MCP_POLYGON_LOGS_KEY] = updated
    return updated


def _resolve_lending_pool_abi_path() -> tuple[Optional[str], Optional[str], Optional[str]]:
    env_value = os.getenv(LENDING_POOL_ABI_PATH_ENV)
    if env_value:
        candidate = os.path.expanduser(env_value)
        if os.path.exists(candidate):
            return candidate, LENDING_POOL_ABI_PATH_ENV, None
        return None, None, candidate
    guessed = guess_default_lending_pool_abi_path()
    if guessed:
        return guessed, "foundry artifact", None
    return None, None, None


def _render_cctp_bridge_section(role_addresses: Dict[str, str], wallet_info: Optional[Dict[str, Any]]) -> None:
    st.divider()
    st.subheader("Owner USDC Tools")
    st.caption(
        "Send USDC from the lending pool owner wallet on ARC or initiate a Circle CCTP bridge to a different chain."
    )

    connected_address = wallet_info.get("address") if isinstance(wallet_info, dict) else None
    default_polygon = _resolve_polygon_address(role_addresses, connected_address)
    default_arc_recipient = connected_address or role_addresses.get("Owner") or ""

    arc_rpc_url = os.getenv(ARC_RPC_ENV)
    private_key = os.getenv(BRIDGE_PRIVATE_KEY_ENV) or os.getenv(PRIVATE_KEY_ENV)
    private_key_source = None
    if os.getenv(BRIDGE_PRIVATE_KEY_ENV):
        private_key_source = BRIDGE_PRIVATE_KEY_ENV
    elif os.getenv(PRIVATE_KEY_ENV):
        private_key_source = PRIVATE_KEY_ENV

    lending_pool_address = os.getenv(LENDING_POOL_ADDRESS_ENV)
    abi_path, abi_source, invalid_abi_path = _resolve_lending_pool_abi_path()

    missing_envs: list[str] = []
    if not arc_rpc_url:
        missing_envs.append(ARC_RPC_ENV)
    if not private_key:
        missing_envs.append(f"{BRIDGE_PRIVATE_KEY_ENV} or {PRIVATE_KEY_ENV}")
    if not lending_pool_address:
        missing_envs.append(LENDING_POOL_ADDRESS_ENV)
    if not abi_path:
        missing_envs.append(f"{LENDING_POOL_ABI_PATH_ENV} (or compile LendingPool)")

    if invalid_abi_path:
        st.warning(
            f"ABI path set via `{LENDING_POOL_ABI_PATH_ENV}` was not found: `{invalid_abi_path}`",
            icon="⚠️",
        )

    if missing_envs:
        st.error("Configure the following settings before continuing: " + ", ".join(missing_envs))
        return

    gas_limit: Optional[int] = None
    gas_limit_raw = os.getenv(GAS_LIMIT_ENV)
    if gas_limit_raw:
        try:
            gas_limit = int(gas_limit_raw, 0)
        except ValueError:
            st.warning(f"Unable to parse `{GAS_LIMIT_ENV}` = {gas_limit_raw}; using estimated gas.")

    gas_price_wei: Optional[int] = None
    gas_price_raw = os.getenv(GAS_PRICE_GWEI_ENV)
    if gas_price_raw:
        try:
            gas_price_wei = int(Decimal(gas_price_raw) * Decimal(1_000_000_000))
        except (InvalidOperation, ValueError):
            st.warning(f"Unable to parse `{GAS_PRICE_GWEI_ENV}` = {gas_price_raw}; using network gas price.")

    polygon_rpc_url = os.getenv(POLYGON_RPC_ENV) or os.getenv("POLYGON_RPC_URL")
    polygon_private_key = os.getenv(POLYGON_PRIVATE_KEY_ENV)
    auto_mint_configured = bool(polygon_rpc_url and polygon_private_key)

    with st.expander("Connection details", expanded=False):
        st.markdown(f"**ARC RPC URL:** `{arc_rpc_url}`")
        st.markdown(f"**LendingPool address:** `{lending_pool_address}`")
        if abi_source:
            st.markdown(f"**ABI path:** `{abi_path}` (source: {abi_source})")
        else:
            st.markdown(f"**ABI path:** `{abi_path}`")
        if private_key_source:
            st.markdown(f"**Owner key source:** `{private_key_source}`")
        if default_polygon:
            st.markdown(f"**Default Polygon recipient:** `{default_polygon}`")
        if gas_limit is not None:
            st.markdown(f"**Gas limit override:** {gas_limit}")
        if gas_price_wei is not None:
            st.markdown(f"**Gas price override:** {gas_price_wei} wei")
        if auto_mint_configured:
            st.markdown("**Polygon auto-mint:** enabled")
        else:
            st.markdown("**Polygon auto-mint:** disabled (manual mint required)")

    st.markdown("### ARC Same-Chain Transfer")
    transfer_state: Optional[Dict[str, Any]] = st.session_state.get(MCP_ARC_TRANSFER_SESSION_KEY)

    with st.form("mcp_arc_transfer_form"):
        recipient_input = st.text_input(
            "ARC recipient address",
            value=default_arc_recipient,
            key="mcp_arc_transfer_recipient",
        ).strip()
        amount_input = st.text_input("Amount to transfer (USDC)", value="0.10", key="mcp_arc_transfer_amount")
        submitted_arc = st.form_submit_button("Send USDC on ARC")

    if submitted_arc:
        st.session_state.pop(MCP_ARC_TRANSFER_SESSION_KEY, None)
        arc_logs: list[str] = []
        try:
            with st.spinner("Broadcasting ARC transfer…"):
                result = transfer_arc_usdc(
                    arc_recipient=recipient_input or "",
                    amount_input=amount_input,
                    rpc_url=arc_rpc_url or "",
                    contract_address=lending_pool_address or "",
                    contract_abi_path=abi_path,
                    private_key=private_key or "",
                    gas_limit=gas_limit,
                    gas_price_wei=gas_price_wei,
                    log=arc_logs.append,
                )
            transfer_state = result.to_state()
            st.session_state[MCP_ARC_TRANSFER_SESSION_KEY] = transfer_state
            st.success(f"USDC sent to `{transfer_state['arc_recipient']}`.", icon="✅")
            if arc_logs:
                with st.expander("ARC transfer log", expanded=False):
                    st.code("\n".join(arc_logs), language="text")
        except BridgeError as err:
            st.error(f"ARC transfer failed: {err}")
            if arc_logs:
                with st.expander("ARC transfer log", expanded=True):
                    st.code("\n".join(arc_logs), language="text")
        except Exception as err:
            st.error(f"Unexpected ARC transfer error: {err}")
            if arc_logs:
                with st.expander("ARC transfer log", expanded=True):
                    st.code("\n".join(arc_logs), language="text")

    transfer_state = st.session_state.get(MCP_ARC_TRANSFER_SESSION_KEY)
    if transfer_state:
        st.markdown(
            f"**Transaction hash:** [`{transfer_state['transfer_tx_hash']}`]"
            f"({transfer_state['transfer_tx_explorer']})"
        )
        if st.button("Clear ARC transfer session", key="mcp_clear_arc_transfer"):
            st.session_state.pop(MCP_ARC_TRANSFER_SESSION_KEY, None)
            st.experimental_rerun()
    else:
        st.info("Submit the form above to transfer USDC between ARC wallets.")

    st.markdown("### Circle CCTP Bridge (ARC → Other Chains)")
    st.caption("Use this section only for cross-chain transfers from ARC via Circle CCTP.")

    polygon_address = st.text_input(
        "Destination Polygon address",
        value=default_polygon or "",
        key="mcp_cctp_polygon_address",
    ).strip()

    if not polygon_address:
        st.info("Enter a Polygon address or connect a wallet above to continue with the bridge.")
        return

    bridge_state: Optional[Dict[str, Any]] = st.session_state.get(MCP_BRIDGE_SESSION_KEY)

    bridge_status_box = st.empty()

    with st.form("mcp_cctp_bridge_form"):
        amount_input = st.text_input("Amount to bridge (USDC)", value="0.10", key="mcp_cctp_amount")
        submitted_bridge = st.form_submit_button("Start ARC → Polygon bridge")

    if submitted_bridge:
        st.session_state.pop(MCP_BRIDGE_SESSION_KEY, None)
        bridge_logs: list[str] = []
        bridge_status_box.info(
            "Submitting bridge transactions… Circle usually needs a few minutes to finalise the attestation."
        )

        def log_to_ui(message: str) -> None:
            bridge_logs.append(message)
            bridge_status_box.code("\n".join(bridge_logs[-40:]), language="text")

        try:
            with st.spinner(
                "Submitting ARC burn. Circle typically finalises the attestation after ~5 minutes."
            ):
                result = initiate_arc_to_polygon_bridge(
                    polygon_address=polygon_address,
                    amount_input=amount_input,
                    rpc_url=arc_rpc_url or "",
                    contract_address=lending_pool_address or "",
                    contract_abi_path=abi_path,
                    private_key=private_key or "",
                    gas_limit=gas_limit,
                    gas_price_wei=gas_price_wei,
                    polygon_rpc_url=polygon_rpc_url,
                    polygon_private_key=polygon_private_key,
                    attestation_poll_interval=ATTESTATION_POLL_INTERVAL,
                    attestation_timeout=ATTESTATION_TIMEOUT,
                    wait_for_attestation=False,
                    attestation_initial_timeout=ATTESTATION_INITIAL_TIMEOUT,
                    log=log_to_ui,
                )
            bridge_state = result.to_state()
            st.session_state[MCP_BRIDGE_SESSION_KEY] = bridge_state
            if bridge_state.get("status") == "complete":
                st.success("Circle attestation received. Continue with the Polygon mint step below.")
            else:
                st.info(
                    "ARC transactions confirmed. Circle attestation is still pending — refresh below once it becomes available."
                )
            bridge_status_box.code("\n".join(bridge_logs[-40:]), language="text")
            if bridge_logs:
                with st.expander("Bridge log", expanded=False):
                    st.code("\n".join(bridge_logs), language="text")
        except BridgeError as err:
            st.error(f"CCTP bridge failed: {err}")
            bridge_status_box.code("\n".join(bridge_logs[-40:]), language="text")
            if bridge_logs:
                with st.expander("Bridge log", expanded=True):
                    st.code("\n".join(bridge_logs), language="text")
        except Exception as err:
            st.error(f"Unexpected bridge error: {err}")
            bridge_status_box.code("\n".join(bridge_logs[-40:]), language="text")
            if bridge_logs:
                with st.expander("Bridge log", expanded=True):
                    st.code("\n".join(bridge_logs), language="text")

    bridge_state = st.session_state.get(MCP_BRIDGE_SESSION_KEY)
    if not bridge_state:
        st.info("Once the burn transaction completes, this section will prepare the Polygon mint transaction.")
        return

    st.markdown("### Step 1 — ARC Transactions")
    st.markdown(
        f"- **Amount:** {bridge_state['amount_usdc']} USDC\n"
        f"- **Prepare transaction:** [`{bridge_state['prepare_tx_hash']}`]"
        f"({bridge_state['prepare_tx_explorer']})\n"
        f"- **Burn transaction:** [`{bridge_state['burn_tx_hash']}`]"
        f"({bridge_state['burn_tx_explorer']})"
    )
    approve_hash = bridge_state.get("approve_tx_hash")
    approve_explorer = bridge_state.get("approve_tx_explorer")
    if approve_hash and approve_explorer:
        st.markdown(f"- **Allowance approval:** [`{approve_hash}`]({approve_explorer})")
    attestation_url = bridge_state.get("attestation_url")
    if attestation_url:
        st.markdown(f"- **Circle attestation API:** [{attestation_url}]({attestation_url})")

    status = bridge_state.get("status", "complete")
    attestation_error = bridge_state.get("attestation_error")

    if status != "complete":
        pending_box = st.empty()
        pending_box.warning(
            "Circle attestation is still pending (Circle waits for ~2,000 ARC blocks ≈ 5 minutes). "
            "Click refresh once you expect it to be available."
        )
        if attestation_error:
            with st.expander("Latest attestation status", expanded=False):
                st.code(attestation_error, language="text")

        refresh_logs: list[str] = []

        def refresh_log_to_ui(message: str) -> None:
            refresh_logs.append(message)
            pending_box.code("\n".join(refresh_logs[-40:]), language="text")

        if st.button("Refresh Circle attestation", key="mcp_refresh_cctp_attestation"):
            try:
                resume_result = resume_arc_to_polygon_bridge(
                    polygon_address=bridge_state["polygon_address"],
                    amount_usdc=bridge_state["amount_usdc"],
                    amount_base_units=bridge_state["amount_base_units"],
                    prepare_tx_hash=bridge_state["prepare_tx_hash"],
                    prepare_tx_explorer=bridge_state["prepare_tx_explorer"],
                    burn_tx_hash=bridge_state["burn_tx_hash"],
                    burn_tx_explorer=bridge_state["burn_tx_explorer"],
                    rpc_url=arc_rpc_url or "",
                    polygon_rpc_url=polygon_rpc_url,
                    polygon_private_key=polygon_private_key,
                    gas_limit=gas_limit,
                    gas_price_wei=gas_price_wei,
                    attestation_poll_interval=ATTESTATION_POLL_INTERVAL,
                    attestation_timeout=ATTESTATION_TIMEOUT,
                    nonce=bridge_state.get("nonce"),
                    approve_tx_hash=bridge_state.get("approve_tx_hash"),
                    approve_tx_explorer=bridge_state.get("approve_tx_explorer"),
                    log=refresh_log_to_ui,
                )
            except BridgeError as err:
                pending_box.info(f"Circle attestation still pending: {err}")
                if refresh_logs:
                    with st.expander("Bridge log", expanded=True):
                        st.code("\n".join(refresh_logs), language="text")
            else:
                st.session_state[MCP_BRIDGE_SESSION_KEY] = resume_result.to_state()
                st.success("Circle attestation is ready. Continue with the Polygon mint below.", icon="✅")
                if refresh_logs:
                    with st.expander("Bridge log", expanded=False):
                        st.code("\n".join(refresh_logs), language="text")
                st.experimental_rerun()
        return

    auto_mint_hash = bridge_state.get("auto_mint_tx_hash")
    auto_mint_explorer = bridge_state.get("auto_mint_tx_explorer")
    auto_mint_error = bridge_state.get("auto_mint_error")

    st.markdown("### Step 2 — Mint USDC on Polygon PoS Amoy")
    if auto_mint_hash:
        st.success(
            f"Polygon mint submitted automatically: `{auto_mint_hash}`",
            icon="✅",
        )
        if auto_mint_explorer:
            st.markdown(f"[View on Polygonscan]({auto_mint_explorer})")
        return

    if auto_mint_error:
        st.warning(f"Automatic Polygon mint failed: {auto_mint_error}. Submit the mint via MetaMask below.")

    message_hex = bridge_state.get("message_hex")
    attestation_hex = bridge_state.get("attestation_hex")
    if not message_hex or not attestation_hex:
        st.error("Attestation payload missing from bridge state. Refresh the attestation above.")
        return
    with st.expander("Message & attestation payload", expanded=False):
        st.code(message_hex, language="text")
        st.code(attestation_hex, language="text")

    tx_request = bridge_state.get("tx_request")
    if tx_request is None:
        st.error("Bridge state missing tx_request payload.")
    else:
        status_box = st.empty()
        log_box = st.empty()
        with st.expander("Polygon transaction payload (advanced)", expanded=False):
            st.json(tx_request)

        polygon_logs: list[str] = st.session_state.get(MCP_POLYGON_LOGS_KEY, [])
        wallet_state = st.session_state.get(MCP_POLYGON_WALLET_STATE_KEY, {})
        wallet_chain_id = _normalise_chain_id(
            (wallet_state or {}).get("chainId") or (wallet_state or {}).get("wallet_chain")
        )
        chain_ready = wallet_chain_id == POLYGON_AMOY_CHAIN_ID
        auto_switch_attempted = bool(st.session_state.get(MCP_POLYGON_AUTO_SWITCH_KEY))
        current_command = st.session_state.get(MCP_POLYGON_COMMAND_KEY)

        if chain_ready:
            if auto_switch_attempted:
                st.session_state.pop(MCP_POLYGON_AUTO_SWITCH_KEY, None)
                st.session_state[MCP_POLYGON_STATUS_KEY] = {
                    "level": "info",
                    "message": "Wallet connected to Polygon PoS Amoy. Ready to submit the mint.",
                }
        else:
            if not auto_switch_attempted and current_command is None:
                polygon_logs = _log_polygon_event(
                    "⚠ Auto-requesting MetaMask network switch to Polygon PoS Amoy…", polygon_logs, level="warning"
                )
                next_seq = st.session_state.get(MCP_POLYGON_COMMAND_SEQ_KEY, 0) + 1
                st.session_state[MCP_POLYGON_COMMAND_SEQ_KEY] = next_seq
                st.session_state[MCP_POLYGON_COMMAND_KEY] = "switch_network"
                st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = {
                    "require_chain_id": POLYGON_AMOY_CHAIN_ID
                }
                st.session_state[MCP_POLYGON_AUTO_SWITCH_KEY] = True
                st.session_state[MCP_POLYGON_STATUS_KEY] = {
                    "level": "warning",
                    "message": "MetaMask network switch requested automatically. Approve the Polygon PoS Amoy switch to continue.",
                }
                current_command = "switch_network"

        if not chain_ready:
            st.session_state.setdefault(MCP_POLYGON_AUTO_SWITCH_KEY, auto_switch_attempted)

        col_submit, col_clear = st.columns([3, 1])
        status_state = st.session_state.get(MCP_POLYGON_STATUS_KEY)
        completion_state = st.session_state.get(MCP_POLYGON_COMPLETE_KEY)
        if isinstance(status_state, dict):
            status_message = status_state.get("message")
            status_level = str(status_state.get("level", "info")).lower()
            if status_message:
                if status_level == "success":
                    st.success(status_message, icon="✅")
                elif status_level == "warning":
                    st.warning(status_message)
                elif status_level == "error":
                    st.error(status_message)
                else:
                    st.info(status_message)
        if isinstance(completion_state, dict):
            completion_message = completion_state.get("message")
            completion_hash = completion_state.get("txHash")
            completion_explorer = completion_state.get("explorer")
            if completion_message:
                st.success(completion_message, icon="✅")
            if completion_hash:
                st.markdown(f"**Polygon tx hash:** `{completion_hash}`")
            if completion_explorer:
                st.markdown(f"[View on Polygonscan]({completion_explorer})")

        if polygon_logs:
            log_box.code("\n".join(polygon_logs[-40:]), language="text")
        else:
            log_box.info("No Polygon activity yet.")

        with col_submit:
            if st.button("Submit mint via MetaMask", key="mcp_polygon_mint_button"):
                next_seq = st.session_state.get(MCP_POLYGON_COMMAND_SEQ_KEY, 0) + 1
                st.session_state[MCP_POLYGON_COMMAND_SEQ_KEY] = next_seq
                st.session_state.pop(MCP_POLYGON_STATUS_KEY, None)
                st.session_state.pop(MCP_POLYGON_COMPLETE_KEY, None)
                if chain_ready:
                    polygon_logs = _log_polygon_event("→ requesting MetaMask signature…", polygon_logs)
                    st.session_state[MCP_POLYGON_PENDING_TX_KEY] = None
                    st.session_state[MCP_POLYGON_COMMAND_KEY] = "send_transaction"
                    st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = {
                        "tx_request": tx_request,
                        "action": "eth_sendTransaction",
                    }
                    st.session_state[MCP_POLYGON_STATUS_KEY] = {
                        "level": "info",
                        "message": "Waiting for MetaMask to confirm the Polygon mint transaction…",
                    }
                else:
                    polygon_logs = _log_polygon_event(
                        "⚠ Requesting MetaMask network switch to Polygon PoS Amoy…", polygon_logs, level="warning"
                    )
                    st.session_state[MCP_POLYGON_PENDING_TX_KEY] = tx_request
                    st.session_state[MCP_POLYGON_COMMAND_KEY] = "switch_network"
                    st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = {
                        "require_chain_id": POLYGON_AMOY_CHAIN_ID
                    }
                    st.session_state[MCP_POLYGON_STATUS_KEY] = {
                        "level": "warning",
                        "message": "MetaMask network switch requested. Approve the Polygon PoS Amoy switch to continue.",
                    }
                st.session_state[MCP_POLYGON_LOGS_KEY] = polygon_logs
        with col_clear:
            if st.button("Clear Polygon log", key="mcp_clear_polygon_log"):
                st.session_state.pop(MCP_POLYGON_LOGS_KEY, None)
                polygon_logs = []
                st.session_state.pop(MCP_POLYGON_STATUS_KEY, None)
                st.session_state.pop(MCP_POLYGON_COMPLETE_KEY, None)

        command = st.session_state.get(MCP_POLYGON_COMMAND_KEY)
        command_sequence = st.session_state.get(MCP_POLYGON_COMMAND_SEQ_KEY)
        command_args = st.session_state.get(MCP_POLYGON_COMMAND_ARGS_KEY) or {}

        component_payload = wallet_command(
            key="mcp_polygon_cctp_receive_headless",
            command=command,
            command_sequence=command_sequence,
            require_chain_id=POLYGON_AMOY_CHAIN_ID,
            tx_request=tx_request if command == "send_transaction" else None,
            action="eth_sendTransaction" if command == "send_transaction" else None,
            preferred_address=polygon_address,
            autoconnect=True,
            command_payload=command_args,
        )

        if component_payload is not None:
            polygon_logger.info("MetaMask payload: %s", component_payload)

        if isinstance(component_payload, dict):
            st.session_state[MCP_POLYGON_WALLET_STATE_KEY] = component_payload
            payload_chain_id = _normalise_chain_id(component_payload.get("chainId"))
            if payload_chain_id is not None:
                wallet_chain_id = payload_chain_id
                chain_ready = wallet_chain_id == POLYGON_AMOY_CHAIN_ID
            if command == "switch_network" and chain_ready:
                st.session_state[MCP_POLYGON_STATUS_KEY] = {
                    "level": "info",
                    "message": "MetaMask switched to Polygon PoS Amoy. Confirm the mint prompt.",
                }
            payload_command = str(component_payload.get("command") or "").lower()
            payload_status = str(component_payload.get("status") or "").lower()
            payload_warning = component_payload.get("warning")
            payload_error = component_payload.get("error")
            payload_tx_hash = component_payload.get("txHash")
            if payload_error:
                st.session_state[MCP_POLYGON_STATUS_KEY] = {
                    "level": "error",
                    "message": f"MetaMask error: {payload_error}",
                }
            elif payload_warning:
                st.session_state[MCP_POLYGON_STATUS_KEY] = {
                    "level": "warning",
                    "message": str(payload_warning),
                }
            elif payload_status:
                if payload_status == "sent" and payload_tx_hash:
                    st.session_state[MCP_POLYGON_STATUS_KEY] = {
                        "level": "success",
                        "message": f"Polygon mint transaction sent: `{payload_tx_hash}`.",
                    }
                    explorer_url = polygon_explorer_url(payload_tx_hash)
                    st.session_state[MCP_POLYGON_COMPLETE_KEY] = {
                        "txHash": payload_tx_hash,
                        "explorer": explorer_url,
                        "message": f"Polygon mint transaction sent: `{payload_tx_hash}`.",
                    }
                elif payload_status == "switched":
                    st.session_state[MCP_POLYGON_STATUS_KEY] = {
                        "level": "info",
                        "message": "MetaMask reports the wallet switched networks. Preparing mint request…",
                    }
                    pending_tx = st.session_state.get(MCP_POLYGON_PENDING_TX_KEY)
                    if pending_tx:
                        polygon_logs = _log_polygon_event(
                            "MetaMask confirmed network switch. Sending Polygon mint transaction request…",
                            polygon_logs,
                        )
                        next_seq = st.session_state.get(MCP_POLYGON_COMMAND_SEQ_KEY, 0) + 1
                        st.session_state[MCP_POLYGON_COMMAND_SEQ_KEY] = next_seq
                        st.session_state[MCP_POLYGON_COMMAND_KEY] = "send_transaction"
                        st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = {
                            "tx_request": pending_tx,
                            "action": "eth_sendTransaction",
                        }
                        st.session_state[MCP_POLYGON_STATUS_KEY] = {
                            "level": "info",
                            "message": "MetaMask switch complete. Awaiting mint confirmation…",
                        }
                        st.experimental_rerun()
                    else:
                        polygon_logs = _log_polygon_event(
                            "MetaMask confirmed network switch. Click 'Submit mint via MetaMask' to continue.",
                            polygon_logs,
                            level="warning",
                        )
                elif payload_status == "rejected":
                    st.session_state[MCP_POLYGON_STATUS_KEY] = {
                        "level": "warning",
                        "message": "MetaMask action rejected. Click submit again to retry.",
                    }
                else:
                    st.session_state[MCP_POLYGON_STATUS_KEY] = {
                        "level": "info",
                        "message": f"MetaMask status: {payload_status}.",
                    }

            if payload_tx_hash and not st.session_state.get(MCP_POLYGON_COMPLETE_KEY):
                explorer_url = polygon_explorer_url(payload_tx_hash)
                st.session_state[MCP_POLYGON_COMPLETE_KEY] = {
                    "txHash": payload_tx_hash,
                    "explorer": explorer_url,
                    "message": f"Polygon mint transaction sent: `{payload_tx_hash}`.",
                }
        else:
            payload_chain_id = None
            polygon_logs = _log_polygon_event(
                f"(info) MetaMask payload received: {component_payload}", polygon_logs
            )

        base_message = (
            "MetaMask must submit this Polygon transaction. Connect your wallet, ensure you have test MATIC, "
            "and confirm the prompt."
        )
        if chain_ready:
            status_box.success("Wallet connected to Polygon PoS Amoy. Ready to submit the mint.")
        elif wallet_chain_id is not None:
            status_box.warning(
                f"Wallet is currently on chain {wallet_chain_id} (0x{wallet_chain_id:x}). "
                "Click the button above to switch to Polygon PoS Amoy before minting."
            )
        else:
            status_box.info(base_message)

        if command == "send_transaction" and isinstance(component_payload, dict):
            if component_payload.get("status") == "sent" and component_payload.get("txHash"):
                mint_hash = component_payload["txHash"]
                polygon_logs = _log_polygon_event(f"✔ mint transaction sent: {mint_hash}", polygon_logs)
                status_box.success("Polygon mint transaction submitted.", icon="✅")
                st.success(f"Mint transaction sent: `{mint_hash}`", icon="✅")
                explorer_url = polygon_explorer_url(mint_hash)
                st.markdown(
                    f"[View on Polygonscan]({explorer_url})",
                    help="Opens the Polygon PoS Amoy transaction in a new tab.",
                )
                st.toast("Polygon mint transaction sent. View it on Polygonscan.", icon="✅")
                st.session_state[MCP_POLYGON_STATUS_KEY] = {
                    "level": "success",
                    "message": f"Polygon mint transaction sent: `{mint_hash}`.",
                }
                st.session_state[MCP_POLYGON_COMPLETE_KEY] = {
                    "txHash": mint_hash,
                    "explorer": explorer_url,
                    "message": f"Polygon mint transaction sent: `{mint_hash}`.",
                }
                st.session_state[MCP_POLYGON_COMMAND_KEY] = None
                st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = None
                st.session_state[MCP_POLYGON_PENDING_TX_KEY] = None
                st.session_state.pop(MCP_POLYGON_AUTO_SWITCH_KEY, None)
            elif component_payload.get("error"):
                error_msg = str(component_payload["error"])
                polygon_logs = _log_polygon_event(f"✖ error: {error_msg}", polygon_logs, level="error")
                status_box.error(error_msg)
                st.session_state[MCP_POLYGON_STATUS_KEY] = {
                    "level": "error",
                    "message": f"Polygon mint failed: {error_msg}",
                }
                st.session_state[MCP_POLYGON_COMMAND_KEY] = None
                st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = None
            elif component_payload.get("warning"):
                warning_msg = str(component_payload["warning"])
                polygon_logs = _log_polygon_event(f"! warning: {warning_msg}", polygon_logs, level="warning")
                status_box.warning(warning_msg)
                st.session_state[MCP_POLYGON_STATUS_KEY] = {
                    "level": "warning",
                    "message": warning_msg,
                }
                st.session_state[MCP_POLYGON_COMMAND_KEY] = None
                st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = None
            else:
                polygon_logs = _log_polygon_event(f"(info) payload: {component_payload}", polygon_logs)
                st.session_state[MCP_POLYGON_STATUS_KEY] = {
                    "level": "info",
                    "message": "Polygon mint request submitted to MetaMask.",
                }
                st.session_state[MCP_POLYGON_COMMAND_KEY] = None
                st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = None
        elif command == "switch_network" and isinstance(component_payload, dict):
            if component_payload.get("error"):
                error_msg = str(component_payload["error"])
                polygon_logs = _log_polygon_event(f"✖ network switch error: {error_msg}", polygon_logs, level="error")
                st.session_state[MCP_POLYGON_COMMAND_KEY] = None
                st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = None
                st.session_state.pop(MCP_POLYGON_AUTO_SWITCH_KEY, None)
                st.session_state[MCP_POLYGON_STATUS_KEY] = {
                    "level": "error",
                    "message": f"MetaMask network switch failed: {error_msg}",
                }
            elif chain_ready:
                polygon_logs = _log_polygon_event(
                    "✔ Wallet switched to Polygon PoS Amoy. Requesting mint signature…", polygon_logs
                )
                pending_tx = st.session_state.get(MCP_POLYGON_PENDING_TX_KEY)
                if pending_tx:
                    next_seq = st.session_state.get(MCP_POLYGON_COMMAND_SEQ_KEY, 0) + 1
                    st.session_state[MCP_POLYGON_COMMAND_SEQ_KEY] = next_seq
                    st.session_state[MCP_POLYGON_COMMAND_KEY] = "send_transaction"
                    st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = {
                        "tx_request": pending_tx,
                        "action": "eth_sendTransaction",
                    }
                    st.session_state[MCP_POLYGON_PENDING_TX_KEY] = None
                    st.toast("MetaMask switched to Polygon PoS Amoy. Confirm the mint.", icon="ℹ️")
                    st.session_state[MCP_POLYGON_STATUS_KEY] = {
                        "level": "info",
                        "message": "MetaMask switched to Polygon PoS Amoy. When prompted, confirm the mint transaction.",
                    }
                    st.experimental_rerun()
                else:
                    st.session_state[MCP_POLYGON_COMMAND_KEY] = None
                    st.session_state[MCP_POLYGON_COMMAND_ARGS_KEY] = None
                    st.toast("Wallet switched to Polygon PoS Amoy. Submit the mint when ready.", icon="ℹ️")
                    st.session_state[MCP_POLYGON_STATUS_KEY] = {
                        "level": "info",
                        "message": "Wallet switched to Polygon PoS Amoy. Submit the mint when ready.",
                    }

        polygon_logs = st.session_state.get(MCP_POLYGON_LOGS_KEY, [])
        if polygon_logs:
            with st.expander("Polygon wallet log", expanded=True):
                st.code("\n".join(polygon_logs), language="text")
        else:
            st.info("No Polygon wallet events yet. Click the button above to send the transaction.")

    st.caption("You will need test MATIC on Polygon Amoy to submit the mint transaction.")

    if st.button("Clear bridge session", key="mcp_clear_cctp_session"):
        st.session_state.pop(MCP_BRIDGE_SESSION_KEY, None)
        st.session_state.pop(MCP_POLYGON_LOGS_KEY, None)
        st.session_state.pop(MCP_POLYGON_STATUS_KEY, None)
        st.session_state.pop(MCP_POLYGON_COMPLETE_KEY, None)
        st.session_state.pop(MCP_POLYGON_PENDING_TX_KEY, None)
        st.session_state.pop(MCP_POLYGON_COMMAND_KEY, None)
        st.session_state.pop(MCP_POLYGON_COMMAND_ARGS_KEY, None)
        st.session_state.pop(MCP_POLYGON_AUTO_SWITCH_KEY, None)
        st.session_state.pop(MCP_POLYGON_WALLET_STATE_KEY, None)
        st.experimental_rerun()


def render_mcp_tools_page() -> None:
    st.title("🧪 Direct MCP Tool Tester")
    st.caption("Run MCP tools for TrustMintSBT and LendingPool.")

    rpc_url = os.getenv(ARC_RPC_ENV)
    private_key_env = os.getenv(PRIVATE_KEY_ENV)
    default_gas_limit = int(os.getenv(GAS_LIMIT_ENV, "200000"))
    gas_price_gwei = os.getenv(GAS_PRICE_GWEI_ENV, "1")

    w3 = get_web3_client(rpc_url)

    st.divider()
    st.subheader("MetaMask Role Assignment")

    chain_id = None
    try:
        chain_id = w3.eth.chain_id if w3 else None
    except Exception:
        chain_id = None

    roles_key = "role_addresses"
    role_addresses: Dict[str, str] = (
        st.session_state.get(roles_key, {})
        if isinstance(st.session_state.get(roles_key), dict)
        else {}
    )
    role_addresses.setdefault("Owner", "")
    role_addresses.setdefault("Lender", "")
    role_addresses.setdefault("Borrower", "")

    wallet_info = connect_wallet(
        key="role_assignment_wallet",
        require_chain_id=chain_id,
        preferred_address=
        role_addresses.get("Owner")
        or role_addresses.get("Lender")
        or role_addresses.get("Borrower"),
        autoconnect=True,
    )

    current_address = wallet_info.get("address") if isinstance(wallet_info, dict) else None
    assignment_col, info_col = st.columns([2, 1])

    with assignment_col:
        role_choice = st.selectbox(
            "Assign connected wallet to role",
            ["Owner", "Lender", "Borrower"],
            key="role_assignment_choice",
        )
        if current_address:
            st.info(f"Connected wallet: {current_address}")
            if st.button("Assign to role", key="assign_role_button"):
                role_addresses[role_choice] = current_address
                st.session_state[roles_key] = role_addresses
                st.toast(f"Assigned {current_address} to {role_choice}", icon="✅")
        else:
            st.warning("Connect MetaMask to assign addresses to roles.")

    with info_col:
        st.caption("Stored role addresses")
        st.json(role_addresses)

    owner_pk = os.getenv(PRIVATE_KEY_ENV)
    lender_pk = os.getenv("LENDER_PRIVATE_KEY")
    borrower_pk = os.getenv("BORROWER_PRIVATE_KEY")

    role_private_keys = {
        "Owner": owner_pk,
        "Lender": lender_pk,
        "Borrower": borrower_pk,
    }

    st.divider()
    st.subheader("Signing sources")
    for role_name in ("Owner", "Lender", "Borrower"):
        pk_value = role_private_keys.get(role_name)
        addr = role_addresses.get(role_name)
        if pk_value:
            st.caption(f"{role_name}: env private key configured for automatic signing.")
        elif addr:
            st.caption(f"{role_name}: MetaMask wallet {addr} will sign when required.")
        else:
            st.caption(f"{role_name}: no signer configured yet; assign a MetaMask wallet above.")

    st.divider()
    st.subheader("TrustMint SBT Tools")

    sbt_address_env = get_sbt_address()
    sbt_address = sbt_address_env[0]
    sbt_env_name = sbt_address_env[1] or SBT_ADDRESS_ENV
    sbt_abi_path = os.getenv(TRUSTMINT_SBT_ABI_PATH_ENV)

    sbt_tools_schema = []
    sbt_function_map = {}
    if sbt_address and sbt_abi_path and w3 is not None:
        sbt_abi = load_contract_abi(sbt_abi_path)
        try:
            sbt_contract = w3.eth.contract(address=Web3.to_checksum_address(sbt_address), abi=sbt_abi)
            sbt_tools_schema, sbt_function_map = build_llm_toolkit(
                w3=w3,
                contract=sbt_contract,
                token_decimals=0,
                private_key=owner_pk,
                default_gas_limit=default_gas_limit,
                gas_price_gwei=gas_price_gwei,
            )
        except Exception as exc:
            st.warning(f"Unable to build SBT toolkit: {exc}")

    if not sbt_tools_schema:
        st.info(
            f"Set `{sbt_env_name}` and `{TRUSTMINT_SBT_ABI_PATH_ENV}` in `.env` to enable TrustMint SBT tools."
        )
    else:
        render_tool_runner(
            sbt_tools_schema,
            sbt_function_map,
            w3,
            key_prefix="sbt",
            role_private_keys=role_private_keys,
            role_addresses=role_addresses,
            tool_role_map=_SBT_TOOL_ROLES,
        )

    st.divider()
    st.subheader("LendingPool Tools")

    pool_address = os.getenv(LENDING_POOL_ADDRESS_ENV)
    pool_abi_path = os.getenv(LENDING_POOL_ABI_PATH_ENV)
    usdc_address = os.getenv(USDC_ADDRESS_ENV)
    usdc_abi_path = os.getenv(USDC_ABI_PATH_ENV)
    usdc_decimals = int(os.getenv(USDC_DECIMALS_ENV, "6"))

    pool_tools_schema = []
    pool_function_map = {}
    if pool_address and pool_abi_path and w3 is not None:
        pool_abi = load_contract_abi(pool_abi_path)
        usdc_abi = load_contract_abi(usdc_abi_path) if usdc_abi_path else None
        try:
            pool_contract = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=pool_abi)
            pool_tools_schema, pool_function_map = build_lending_pool_toolkit(
                w3=w3,
                pool_contract=pool_contract,
                token_decimals=usdc_decimals,
                native_decimals=18,
                private_key=owner_pk,
                default_gas_limit=default_gas_limit,
                gas_price_gwei=gas_price_gwei,
                role_addresses=role_addresses,
                role_private_keys=role_private_keys,
            )
        except Exception as exc:
            st.warning(f"Unable to build LendingPool toolkit: {exc}")

    if not pool_tools_schema:
        st.info(
            "Set `LENDING_POOL_ADDRESS`, `LENDING_POOL_ABI_PATH`, and optional USDC env vars to enable LendingPool tools."
        )
    else:
        parameter_defaults = {
            "deposit": {"amount": 0.1},
            "withdraw": {"amount": 0.1},
            "openLoan": {"principal": 0.1, "term_seconds": 604800},
        }
        render_tool_runner(
            pool_tools_schema,
            pool_function_map,
            w3,
            key_prefix="pool",
            parameter_defaults=parameter_defaults,
            role_private_keys=role_private_keys,
            role_addresses=role_addresses,
            tool_role_map=_POOL_TOOL_ROLES,
        )

    _render_cctp_bridge_section(role_addresses, wallet_info)
