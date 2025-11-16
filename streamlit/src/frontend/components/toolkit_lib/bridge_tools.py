from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from ..cctp_bridge import (
    POLYGON_AMOY_CHAIN_ID,
    BridgeError,
    initiate_arc_to_polygon_bridge,
    resume_arc_to_polygon_bridge,
    transfer_arc_usdc,
)
from ..config import (
    ARC_RPC_ENV,
    BRIDGE_PRIVATE_KEY_ENV,
    GAS_LIMIT_ENV,
    GAS_PRICE_GWEI_ENV,
    LENDING_POOL_ABI_PATH_ENV,
    LENDING_POOL_ADDRESS_ENV,
    POLYGON_PRIVATE_KEY_ENV,
    POLYGON_RPC_ENV,
    PRIVATE_KEY_ENV,
)
from ..toolkit_lib.config_utils import resolve_lending_pool_abi_path
from ..toolkit_lib.messages import tool_error, tool_success
from ..mcp_lib.constants import (
    MCP_ARC_TRANSFER_SESSION_KEY,
    MCP_BRIDGE_SESSION_KEY,
    MCP_POLYGON_COMPLETE_KEY,
    MCP_POLYGON_LOGS_KEY,
    MCP_POLYGON_STATUS_KEY,
    ATTESTATION_POLL_INTERVAL,
    ATTESTATION_TIMEOUT,
    ATTESTATION_INITIAL_TIMEOUT,
)


@dataclass
class BridgeConfig:
    arc_rpc_url: str
    lending_pool_address: str
    abi_path: str
    private_key: str
    gas_limit: Optional[int]
    gas_price_wei: Optional[int]
    polygon_rpc_url: Optional[str]
    polygon_private_key: Optional[str]


def _parse_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def _parse_gas_price(gwei_value: Optional[str]) -> Optional[int]:
    if not gwei_value:
        return None
    try:
        decimal_value = Decimal(gwei_value)
        return int(decimal_value * Decimal(1_000_000_000))
    except (InvalidOperation, ValueError):
        return None


def _load_bridge_config() -> Tuple[Optional[BridgeConfig], Optional[str]]:
    missing_envs: List[str] = []
    arc_rpc_url = os.getenv(ARC_RPC_ENV)
    if not arc_rpc_url:
        missing_envs.append(ARC_RPC_ENV)

    private_key = os.getenv(BRIDGE_PRIVATE_KEY_ENV) or os.getenv(PRIVATE_KEY_ENV)
    if not private_key:
        missing_envs.append(f"{BRIDGE_PRIVATE_KEY_ENV} or {PRIVATE_KEY_ENV}")

    lending_pool_address = os.getenv(LENDING_POOL_ADDRESS_ENV)
    if not lending_pool_address:
        missing_envs.append(LENDING_POOL_ADDRESS_ENV)

    abi_env_value = os.getenv(LENDING_POOL_ABI_PATH_ENV)
    abi_path, abi_source, invalid_path = resolve_lending_pool_abi_path(abi_env_value)
    if invalid_path:
        return (
            None,
            f"ABI path set via `{LENDING_POOL_ABI_PATH_ENV}` was not found: `{invalid_path}`",
        )
    if not abi_path:
        missing_envs.append(f"{LENDING_POOL_ABI_PATH_ENV} (or compile LendingPool)")

    if missing_envs:
        return None, "Configure the following settings before continuing: " + ", ".join(
            missing_envs
        )

    gas_limit = _parse_int(os.getenv(GAS_LIMIT_ENV))
    gas_price_wei = _parse_gas_price(os.getenv(GAS_PRICE_GWEI_ENV))

    polygon_rpc_url = os.getenv(POLYGON_RPC_ENV) or os.getenv("POLYGON_RPC_URL")
    polygon_private_key = os.getenv(POLYGON_PRIVATE_KEY_ENV)

    return (
        BridgeConfig(
            arc_rpc_url=arc_rpc_url or "",
            lending_pool_address=lending_pool_address or "",
            abi_path=abi_path or "",
            private_key=private_key or "",
            gas_limit=gas_limit,
            gas_price_wei=gas_price_wei,
            polygon_rpc_url=polygon_rpc_url,
            polygon_private_key=polygon_private_key,
        ),
        None,
    )


def _bridge_logs_payload(logs: List[str]) -> Dict[str, Any]:
    return {"logs": logs[-40:], "logCount": len(logs)}


def build_bridge_toolkit() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    handlers: Dict[str, Any] = {}

    def register(
        name: str, description: str, parameters: Dict[str, Any], handler: Any
    ) -> None:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            }
        )
        handlers[name] = handler

    def arc_transfer_tool(arc_recipient: str, amount: str) -> str:

        config, error = _load_bridge_config()
        if error or config is None:
            return tool_error(error or "Bridge configuration invalid.")

        logs: List[str] = []
        try:
            result = transfer_arc_usdc(
                arc_recipient=arc_recipient,
                amount_input=amount,
                rpc_url=config.arc_rpc_url,
                contract_address=config.lending_pool_address,
                contract_abi_path=config.abi_path,
                private_key=config.private_key,
                gas_limit=config.gas_limit,
                gas_price_wei=config.gas_price_wei,
                log=lambda msg: logs.append(str(msg)),
            )
        except BridgeError as exc:
            return tool_error(str(exc), **_bridge_logs_payload(logs))

        state = result.to_state()
        st.session_state[MCP_ARC_TRANSFER_SESSION_KEY] = state

        return tool_success({"transfer": state, **_bridge_logs_payload(logs)})

    register(
        "arcTransfer",
        "Send USDC from the LendingPool owner wallet to an ARC recipient.",
        {
            "type": "object",
            "properties": {
                "arc_recipient": {
                    "type": "string",
                    "description": "ARC recipient wallet address.",
                },
                "amount": {
                    "type": "string",
                    "description": "Amount of USDC to transfer (e.g., 0.10).",
                },
            },
            "required": ["arc_recipient", "amount"],
        },
        arc_transfer_tool,
    )

    def get_arc_transfer_state_tool() -> str:
        state = st.session_state.get(MCP_ARC_TRANSFER_SESSION_KEY)
        if not state:
            return tool_error("No ARC transfer session found.")
        return tool_success({"transfer": state})

    register(
        "getArcTransferState",
        "Return the last ARC same-chain transfer state if available.",
        {"type": "object", "properties": {}, "required": []},
        lambda: get_arc_transfer_state_tool(),
    )

    def clear_arc_transfer_tool() -> str:
        st.session_state.pop(MCP_ARC_TRANSFER_SESSION_KEY, None)
        return tool_success({"message": "Cleared ARC transfer session."})

    register(
        "clearArcTransferState",
        "Clear the stored ARC same-chain transfer session.",
        {"type": "object", "properties": {}, "required": []},
        lambda: clear_arc_transfer_tool(),
    )

    def start_bridge_tool(
        polygon_address: str, amount: str, wait_for_attestation: bool = False
    ) -> str:

        config, error = _load_bridge_config()
        if error or config is None:
            return tool_error(error or "Bridge configuration invalid.")

        logs: List[str] = []
        try:
            result = initiate_arc_to_polygon_bridge(
                polygon_address=polygon_address,
                amount_input=amount,
                rpc_url=config.arc_rpc_url,
                contract_address=config.lending_pool_address,
                contract_abi_path=config.abi_path,
                private_key=config.private_key,
                gas_limit=config.gas_limit,
                gas_price_wei=config.gas_price_wei,
                polygon_rpc_url=config.polygon_rpc_url,
                polygon_private_key=config.polygon_private_key,
                attestation_poll_interval=ATTESTATION_POLL_INTERVAL,
                attestation_timeout=ATTESTATION_TIMEOUT,
                wait_for_attestation=wait_for_attestation,
                attestation_initial_timeout=ATTESTATION_INITIAL_TIMEOUT,
                log=lambda msg: logs.append(str(msg)),
            )
        except BridgeError as exc:
            return tool_error(str(exc), **_bridge_logs_payload(logs))

        state = result.to_state()
        state["status"] = result.status
        st.session_state[MCP_BRIDGE_SESSION_KEY] = state

        payload: Dict[str, Any] = {
            "bridge": state,
            **_bridge_logs_payload(logs),
        }
        return tool_success(payload)

    register(
        "startArcPolygonBridge",
        "Start the ARC → Polygon Circle CCTP bridge.",
        {
            "type": "object",
            "properties": {
                "polygon_address": {
                    "type": "string",
                    "description": "Destination Polygon wallet address.",
                },
                "amount": {
                    "type": "string",
                    "description": "Amount of USDC to bridge (e.g., 0.10).",
                },
                "wait_for_attestation": {
                    "type": "boolean",
                    "description": "If true, wait for Circle attestation before returning.",
                    "default": False,
                },
            },
            "required": ["polygon_address", "amount"],
        },
        start_bridge_tool,
    )

    def get_bridge_state_tool() -> str:
        state = st.session_state.get(MCP_BRIDGE_SESSION_KEY)
        if not state:
            return tool_error("No active bridge session.")
        return tool_success({"bridge": state})

    register(
        "getBridgeState",
        "Return the current Circle CCTP bridge session state.",
        {"type": "object", "properties": {}, "required": []},
        lambda: get_bridge_state_tool(),
    )

    def resume_bridge_tool() -> str:

        config, error = _load_bridge_config()
        if error or config is None:
            return tool_error(error or "Bridge configuration invalid.")

        bridge_state = st.session_state.get(MCP_BRIDGE_SESSION_KEY)
        if not isinstance(bridge_state, dict):
            return tool_error("No bridge session available to resume.")

        required_keys = [
            "polygon_address",
            "amount_usdc",
            "amount_base_units",
            "prepare_tx_hash",
            "prepare_tx_explorer",
            "burn_tx_hash",
            "burn_tx_explorer",
        ]
        missing = [key for key in required_keys if not bridge_state.get(key)]
        if missing:
            return tool_error(
                f"Bridge session is missing required fields: {', '.join(missing)}"
            )

        logs: List[str] = []
        try:
            result = resume_arc_to_polygon_bridge(
                polygon_address=bridge_state["polygon_address"],
                amount_usdc=str(bridge_state["amount_usdc"]),
                amount_base_units=int(bridge_state["amount_base_units"]),
                prepare_tx_hash=str(bridge_state["prepare_tx_hash"]),
                prepare_tx_explorer=str(bridge_state["prepare_tx_explorer"]),
                burn_tx_hash=str(bridge_state["burn_tx_hash"]),
                burn_tx_explorer=str(bridge_state["burn_tx_explorer"]),
                rpc_url=config.arc_rpc_url,
                polygon_rpc_url=config.polygon_rpc_url,
                polygon_private_key=config.polygon_private_key,
                gas_limit=config.gas_limit,
                gas_price_wei=config.gas_price_wei,
                nonce=bridge_state.get("nonce"),
                approve_tx_hash=bridge_state.get("approve_tx_hash"),
                approve_tx_explorer=bridge_state.get("approve_tx_explorer"),
                attestation_poll_interval=ATTESTATION_POLL_INTERVAL,
                attestation_timeout=ATTESTATION_TIMEOUT,
                log=lambda msg: logs.append(str(msg)),
            )
        except BridgeError as exc:
            return tool_error(str(exc), **_bridge_logs_payload(logs))

        state = result.to_state()
        st.session_state[MCP_BRIDGE_SESSION_KEY] = state

        payload: Dict[str, Any] = {
            "bridge": state,
            **_bridge_logs_payload(logs),
        }
        return tool_success(payload)

    register(
        "resumeArcPolygonBridge",
        "Resume Circle attestation polling for an existing bridge session.",
        {"type": "object", "properties": {}, "required": []},
        lambda: resume_bridge_tool(),
    )

    def prepare_polygon_mint_tool() -> str:
        # Check current network FIRST
        from ..session import DEFAULT_SESSION_KEY
        cached_wallet = st.session_state.get(DEFAULT_SESSION_KEY, {})
        current_chain_id = cached_wallet.get("chainId") if isinstance(cached_wallet, dict) else None
        
        # Convert hex to int if needed
        if current_chain_id and isinstance(current_chain_id, str):
            try:
                if current_chain_id.startswith("0x"):
                    current_chain_id = int(current_chain_id, 16)
                else:
                    current_chain_id = int(current_chain_id)
            except:
                pass
        
        # Check if on Polygon network
        if current_chain_id != POLYGON_AMOY_CHAIN_ID:
            return tool_error(
                f"Wrong network! You must switch to Polygon network (chainId {POLYGON_AMOY_CHAIN_ID}) before preparing the mint transaction. "
                f"Current network chainId: {current_chain_id}. "
                f"Please call ensureWalletNetwork(target_network='POLYGON') first, wait for confirmation, then try again."
            )
        
        bridge_state = st.session_state.get(MCP_BRIDGE_SESSION_KEY)
        if not isinstance(bridge_state, dict):
            return tool_error("No bridge session to prepare Polygon mint for.")

        message = bridge_state.get("message_hex")
        attestation = bridge_state.get("attestation_hex")
        tx_request = bridge_state.get("tx_request")
        polygon_address = bridge_state.get("polygon_address")

        if not message or not attestation:
            return tool_error(
                "Bridge session missing attestation payload. Call `resumeArcPolygonBridge` first."
            )
        if not tx_request:
            return tool_error("Bridge session missing transaction request payload.")

        payload = {
            "bridge": bridge_state,
            "metamask": {
                "tx_request": tx_request,
                "action": "eth_sendTransaction",
                "chainId": POLYGON_AMOY_CHAIN_ID,
            },
        }
        if polygon_address:
            payload["metamask"]["from"] = polygon_address
        st.session_state.setdefault(
            MCP_POLYGON_STATUS_KEY, {"level": "info", "message": "Polygon mint ready on correct network."}
        )
        st.session_state.pop(MCP_POLYGON_COMPLETE_KEY, None)
        st.session_state.pop(MCP_POLYGON_LOGS_KEY, None)
        return tool_success(payload)

    register(
        "preparePolygonMint",
        "Prepare a MetaMask transaction request to mint bridged USDC on Polygon.",
        {"type": "object", "properties": {}, "required": []},
        lambda: prepare_polygon_mint_tool(),
    )

    def clear_bridge_state_tool() -> str:
        st.session_state.pop(MCP_BRIDGE_SESSION_KEY, None)
        st.session_state.pop(MCP_POLYGON_LOGS_KEY, None)
        st.session_state.pop(MCP_POLYGON_STATUS_KEY, None)
        st.session_state.pop(MCP_POLYGON_COMPLETE_KEY, None)
        return tool_success({"message": "Cleared bridge session state."})

    register(
        "clearBridgeState",
        "Clear stored Circle CCTP bridge session data.",
        {"type": "object", "properties": {}, "required": []},
        lambda: clear_bridge_state_tool(),
    )

    return tools, handlers
