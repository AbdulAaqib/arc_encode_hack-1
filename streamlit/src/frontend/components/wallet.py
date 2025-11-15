from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional

import os
import streamlit as st

from . import config
from .cctp_bridge import (
    POLYGON_AMOY_CHAIN_ID,
    BridgeError,
    guess_default_lending_pool_abi_path,
    initiate_arc_to_polygon_bridge,
    polygon_explorer_url,
    resume_arc_to_polygon_bridge,
    transfer_arc_usdc
)
from .wallet_connect_component import connect_wallet, wallet_command
from .mcp_lib.rerun import st_rerun
from .session import DEFAULT_SESSION_KEY
ARC_CHAIN_ID_ENV = "ARC_CHAIN_ID"
BRIDGE_SESSION_KEY = "cctp_bridge_result"
ARC_TRANSFER_SESSION_KEY = "arc_transfer_result"

ATTESTATION_POLL_INTERVAL = 5
ATTESTATION_TIMEOUT = 600
ATTESTATION_INITIAL_TIMEOUT = 30

POLYGON_COMMAND_STATE_KEY = "polygon_wallet_command"
POLYGON_COMMAND_SEQ_KEY = "polygon_wallet_command_seq"
POLYGON_COMMAND_ARGS_KEY = "polygon_wallet_command_args"


def _resolve_chain_id() -> Optional[int]:
    raw = os.getenv(ARC_CHAIN_ID_ENV)
    if not raw:
        return None
    try:
        return int(raw, 0)
    except ValueError:
        return None


def _resolve_polygon_address(wallet_info: Optional[Dict[str, Any]]) -> Optional[str]:
    if not wallet_info:
        return None
    address = wallet_info.get("address")
    return str(address) if address else None


def _resolve_abi_path() -> tuple[Optional[str], Optional[str], Optional[str]]:
    env_value = os.getenv(config.LENDING_POOL_ABI_PATH_ENV)
    if env_value:
        candidate = Path(env_value).expanduser()
        if candidate.exists():
            return str(candidate), config.LENDING_POOL_ABI_PATH_ENV, None
        return None, None, str(candidate)
    guessed = guess_default_lending_pool_abi_path()
    if guessed:
        return guessed, "foundry artifact", None
    return None, None, None


def _render_cctp_bridge(wallet_info: Optional[Dict[str, Any]]) -> None:
    st.divider()
    st.subheader("Owner USDC Tools")
    st.caption("Send USDC from the lending pool owner wallet on ARC or initiate a Circle CCTP bridge to another chain.")

    arc_rpc_url = os.getenv(config.ARC_RPC_ENV)
    private_key = os.getenv(config.BRIDGE_PRIVATE_KEY_ENV) or os.getenv(config.PRIVATE_KEY_ENV)
    private_key_source = None
    if os.getenv(config.BRIDGE_PRIVATE_KEY_ENV):
        private_key_source = config.BRIDGE_PRIVATE_KEY_ENV
    elif os.getenv(config.PRIVATE_KEY_ENV):
        private_key_source = config.PRIVATE_KEY_ENV

    lending_pool_address, lending_pool_env = config.get_lending_pool_address()
    lending_pool_env = lending_pool_env or config.LENDING_POOL_ADDRESS_ENV

    abi_path, abi_source, invalid_abi_path = _resolve_abi_path()

    missing_envs: list[str] = []
    if not arc_rpc_url:
        missing_envs.append(config.ARC_RPC_ENV)
    if not private_key:
        missing_envs.append(f"{config.BRIDGE_PRIVATE_KEY_ENV} or {config.PRIVATE_KEY_ENV}")
    if not lending_pool_address:
        missing_envs.append(lending_pool_env)
    if not abi_path:
        missing_envs.append(f"{config.LENDING_POOL_ABI_PATH_ENV} (or compile LendingPool)")

    if invalid_abi_path:
        st.warning(
            f"ABI path set via `{config.LENDING_POOL_ABI_PATH_ENV}` was not found: `{invalid_abi_path}`",
            icon="⚠️",
        )

    if missing_envs:
        st.error("Configure the following settings first: " + ", ".join(missing_envs))
        return

    gas_limit: Optional[int] = None
    gas_limit_raw = os.getenv(config.GAS_LIMIT_ENV)
    if gas_limit_raw:
        try:
            gas_limit = int(gas_limit_raw, 0)
        except ValueError:
            st.warning(f"Unable to parse `{config.GAS_LIMIT_ENV}` = {gas_limit_raw}; using estimated gas.")

    gas_price_wei: Optional[int] = None
    gas_price_raw = os.getenv(config.GAS_PRICE_GWEI_ENV)
    if gas_price_raw:
        try:
            gas_price_wei = int(Decimal(gas_price_raw) * Decimal(1_000_000_000))
        except (InvalidOperation, ValueError):
            st.warning(f"Unable to parse `{config.GAS_PRICE_GWEI_ENV}` = {gas_price_raw}; using network gas price.")

    polygon_rpc_url = os.getenv(config.POLYGON_RPC_ENV) or os.getenv("POLYGON_RPC_URL")
    polygon_private_key = os.getenv(config.POLYGON_PRIVATE_KEY_ENV)
    auto_mint_configured = bool(polygon_rpc_url and polygon_private_key)

    polygon_address = _resolve_polygon_address(wallet_info)
    arc_default_recipient = wallet_info.get("address") if isinstance(wallet_info, dict) else ""

    with st.expander("Connection details", expanded=False):
        st.markdown(f"**ARC RPC URL:** `{arc_rpc_url}`")
        st.markdown(f"**LendingPool address:** `{lending_pool_address}`")
        if abi_source:
            st.markdown(f"**ABI path:** `{abi_path}` (source: {abi_source})")
        else:
            st.markdown(f"**ABI path:** `{abi_path}`")
        if private_key_source:
            st.markdown(f"**Owner key source:** `{private_key_source}`")
        if polygon_address:
            st.markdown(f"**Polygon recipient (auto):** `{polygon_address}`")
        if gas_limit is not None:
            st.markdown(f"**Gas limit override:** {gas_limit}")
        if gas_price_wei is not None:
            st.markdown(f"**Gas price override:** {gas_price_wei} wei")
        if auto_mint_configured:
            st.markdown("**Polygon auto-mint:** enabled")
        else:
            st.markdown("**Polygon auto-mint:** disabled (manual mint required)")

    st.markdown("### ARC Same-Chain Transfer")
    transfer_state: Optional[Dict[str, Any]] = st.session_state.get(ARC_TRANSFER_SESSION_KEY)

    with st.form("arc_transfer_form"):
        default_recipient = arc_default_recipient or ""
        recipient_input = st.text_input("ARC recipient address", value=default_recipient)
        amount_input = st.text_input("Amount to transfer (USDC)", value="0.10")
        submitted_arc = st.form_submit_button("Send USDC on ARC")

    if submitted_arc:
        st.session_state.pop(ARC_TRANSFER_SESSION_KEY, None)
        arc_logs: list[str] = []
        try:
            with st.spinner("Submitting on-chain transfer…"):
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
            st.session_state[ARC_TRANSFER_SESSION_KEY] = transfer_state
            st.success(
                f"USDC sent to `{transfer_state['arc_recipient']}`.",
                icon="✅",
            )
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

    transfer_state = st.session_state.get(ARC_TRANSFER_SESSION_KEY)
    if transfer_state:
        st.markdown(
            f"**Transaction hash:** [`{transfer_state['transfer_tx_hash']}`]"
            f"({transfer_state['transfer_tx_explorer']})"
        )
        if st.button("Clear transfer session", key="clear_arc_transfer"):
            st.session_state.pop(ARC_TRANSFER_SESSION_KEY, None)
            st_rerun()
    else:
        st.info("Submit the form above to transfer USDC to another ARC wallet.")

    st.markdown("### Circle CCTP Bridge (ARC → Other Chains)")
    st.caption("Use this section only when bridging from ARC to another supported chain via Circle CCTP.")

    if not polygon_address:
        st.info("Connect a wallet above to populate the destination Polygon address before bridging.")
        return

    bridge_state: Optional[Dict[str, Any]] = st.session_state.get(BRIDGE_SESSION_KEY)

    bridge_status_box = st.empty()

    with st.form("cctp_bridge_form"):
        st.markdown(f"**Destination Polygon address:** `{polygon_address}`")
        amount_input = st.text_input("Amount to bridge (USDC)", value="0.10")
        submitted_bridge = st.form_submit_button("Start ARC → Polygon bridge")

    if submitted_bridge:
        st.session_state.pop(BRIDGE_SESSION_KEY, None)
        bridge_logs: list[str] = []
        bridge_status_box.info(
            "Submitting bridge transactions… Circle typically needs a few minutes to finalise the attestation."
        )

        def log_to_ui(message: str) -> None:
            bridge_logs.append(message)
            # show the most recent messages to avoid huge blocks of text
            bridge_status_box.code("\n".join(bridge_logs[-40:]), language="text")

        try:
            with st.spinner(
                "Submitting CCTP transactions. Circle attestation typically finalises after ~5 minutes."
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
            st.session_state[BRIDGE_SESSION_KEY] = bridge_state
            if bridge_state.get("status") == "complete":
                st.success("Circle attestation received. Continue with the Polygon mint step below.")
            else:
                st.info(
                    "ARC transactions confirmed. Circle attestation is still pending — refresh below once finality is reached."
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

    bridge_state = st.session_state.get(BRIDGE_SESSION_KEY)
    if not bridge_state:
        st.info("Once the bridge transaction completes, this section will prepare the Polygon mint transaction.")
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
        st.markdown(
            f"- **Allowance approval:** [`{approve_hash}`]({approve_explorer})"
        )
    attestation_url = bridge_state.get("attestation_url")
    if attestation_url:
        st.markdown(f"- **Circle attestation API:** [{attestation_url}]({attestation_url})")

    st.markdown("### Step 2 — Mint USDC on Polygon PoS Amoy")

    status = bridge_state.get("status", "complete")
    attestation_error = bridge_state.get("attestation_error")

    if status != "complete":
        pending_box = st.empty()
        pending_box.warning(
            "Circle attestation not ready yet. Expect roughly 2,000 ARC blocks of finality (≈5 minutes). "
            "Click the button below to refresh once Circle publishes the attestation."
        )
        if attestation_error:
            with st.expander("Latest attestation status", expanded=False):
                st.code(attestation_error, language="text")

        refresh_logs: list[str] = []

        def refresh_log_to_ui(message: str) -> None:
            refresh_logs.append(message)
            pending_box.code("\n".join(refresh_logs[-40:]), language="text")

        if st.button("Refresh Circle attestation", key="refresh_cctp_attestation"):
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
                updated_state = resume_result.to_state()
                st.session_state[BRIDGE_SESSION_KEY] = updated_state
                st.success("Circle attestation is ready. Continue with the Polygon mint step below.", icon="✅")
                if refresh_logs:
                    with st.expander("Bridge log", expanded=False):
                        st.code("\n".join(refresh_logs), language="text")
                st_rerun()
        return

    message_hex = bridge_state.get("message_hex")
    attestation_hex = bridge_state.get("attestation_hex")
    if not message_hex or not attestation_hex:
        st.error("Attestation payload missing from bridge state. Refresh the attestation above.")
        if st.button("Clear bridge session", key="clear_cctp_session_missing_payload"):
            st.session_state.pop(BRIDGE_SESSION_KEY, None)
            st_rerun()
        return

    with st.expander("Message & attestation payload", expanded=False):
        st.code(message_hex, language="text")
        st.code(attestation_hex, language="text")

    tx_request = bridge_state.get("tx_request")
    if tx_request is None:
        st.error("Bridge state missing tx_request payload.")
    else:
        with st.expander("Polygon transaction payload (advanced)", expanded=False):
            st.json(tx_request)

        status_box = st.empty()
        status_box.info(
            "MetaMask must submit this Polygon transaction. Make sure your wallet is connected, switched to Polygon "
            "PoS Amoy, and has test MATIC before sending."
        )

        polygon_logs: list[str] = st.session_state.get("polygon_wallet_logs", [])

        action_col, clear_col = st.columns([3, 1])
        with action_col:
            if st.button("Submit mint via MetaMask", key="polygon_mint_button"):
                polygon_logs.append("→ requesting MetaMask signature…")
                st.session_state["polygon_wallet_logs"] = polygon_logs
                next_seq = st.session_state.get(POLYGON_COMMAND_SEQ_KEY, 0) + 1
                st.session_state[POLYGON_COMMAND_SEQ_KEY] = next_seq
                st.session_state[POLYGON_COMMAND_STATE_KEY] = "send_transaction"
                st.session_state[POLYGON_COMMAND_ARGS_KEY] = {
                    "tx_request": tx_request,
                    "action": "eth_sendTransaction",
                }

        with clear_col:
            if st.button("Clear Polygon log", key="clear_polygon_log"):
                st.session_state.pop("polygon_wallet_logs", None)
                polygon_logs = []

        command = st.session_state.get(POLYGON_COMMAND_STATE_KEY)
        command_sequence = st.session_state.get(POLYGON_COMMAND_SEQ_KEY)
        command_args = st.session_state.get(POLYGON_COMMAND_ARGS_KEY) or {}

        component_payload = wallet_command(
            key="polygon_cctp_receive_headless",
            command=command,
            command_sequence=command_sequence,
            require_chain_id=POLYGON_AMOY_CHAIN_ID,
            tx_request=tx_request if command == "send_transaction" else None,
            action="eth_sendTransaction" if command == "send_transaction" else None,
            preferred_address=polygon_address,
            autoconnect=True,
            command_payload=command_args,
        )

        if command and component_payload:
            if component_payload.get("status") == "sent" and component_payload.get("txHash"):
                mint_hash = component_payload["txHash"]
                polygon_logs.append(f"✔ mint transaction sent: {mint_hash}")
                st.session_state["polygon_wallet_logs"] = polygon_logs
                status_box.success("Polygon mint transaction submitted.", icon="✅")
                st.success(f"Mint transaction sent: `{mint_hash}`", icon="✅")
                st.markdown(f"[View on Polygonscan]({polygon_explorer_url(mint_hash)})")
            elif component_payload.get("error"):
                error_msg = str(component_payload["error"])
                polygon_logs.append(f"✖ error: {error_msg}")
                st.session_state["polygon_wallet_logs"] = polygon_logs
                status_box.error(error_msg)
            elif component_payload.get("warning"):
                warning_msg = str(component_payload["warning"])
                polygon_logs.append(f"! warning: {warning_msg}")
                st.session_state["polygon_wallet_logs"] = polygon_logs
                status_box.warning(warning_msg)
            else:
                polygon_logs.append(f"(info) payload: {component_payload}")
                st.session_state["polygon_wallet_logs"] = polygon_logs
            st.session_state[POLYGON_COMMAND_STATE_KEY] = None
            st.session_state[POLYGON_COMMAND_ARGS_KEY] = None

        polygon_logs = st.session_state.get("polygon_wallet_logs", [])
        if polygon_logs:
            with st.expander("Polygon wallet log", expanded=True):
                st.code("\n".join(polygon_logs), language="text")
        else:
            st.info("No Polygon wallet events yet. Click the button above to send the transaction.")

    st.caption("You will need test MATIC on Polygon Amoy to submit the mint transaction.")

    if st.button("Clear bridge session", key="clear_cctp_session"):
        st.session_state.pop(BRIDGE_SESSION_KEY, None)
        st_rerun()


def render_wallet_page() -> None:
    """Render the wallet connect page that bridges MetaMask to Streamlit."""

    st.title("🔐 Wallet Connect")
    st.caption("Use your injected wallet (MetaMask, Rabby, etc.) directly inside Streamlit.")

    chain_id = _resolve_chain_id()
    if chain_id is None:
        st.error(
            "Environment variable `ARC_CHAIN_ID` is not set or invalid. Set it to a decimal or hex chain ID "
            "before using the wallet connector."
        )
        st.stop()

    col_left, col_right = st.columns([2, 1])
    with col_right:
        st.subheader("Session State")
        stored: Dict[str, Any] = st.session_state.get(DEFAULT_SESSION_KEY, {})  # type: ignore[assignment]
        if stored.get("isConnected") and stored.get("address"):
            st.success(f"Cached address: {stored['address']}")
            st.json(stored)
        else:
            st.info("No wallet cached yet.")

    wallet_info: Optional[Dict[str, Any]] = None
    with col_left:
        st.subheader("Connect")
        st.caption(f"Required chain ID: `{chain_id}` (from ARC_CHAIN_ID)")

        wallet_info = connect_wallet(key="wallet_connect", require_chain_id=chain_id)

        st.write(":ledger: Component payload")
        st.json(wallet_info)

        if wallet_info and wallet_info.get("isConnected"):
            st.success(f"Connected wallet: {wallet_info.get('address')}")

            if st.button("Store in session", key="cache_wallet"):
                st.session_state[DEFAULT_SESSION_KEY] = wallet_info
                st.toast("Wallet cached in session_state", icon="✅")
        else:
            st.warning("Connect with MetaMask using the button above.")

    _render_cctp_bridge(wallet_info)

    st.divider()
    st.subheader("Tips")
    st.markdown(
        """
        - Install an injected wallet such as MetaMask in your browser.
        - Ensure `ARC_CHAIN_ID` matches the network you expect the wallet to use.
        - Use the JSON payload above from Python to drive downstream logic (contract calls, gating, etc.).
        """
    )
