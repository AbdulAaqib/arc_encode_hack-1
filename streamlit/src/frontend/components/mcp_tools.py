"""MCP Tools: Direct MCP Tool Tester for TrustMintSBT and LendingPool."""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict
from time import time

import streamlit as st
from web3 import Web3
from web3.contract import Contract

from .config import (
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
    get_sbt_address,
)
from .web3_utils import get_web3_client, load_contract_abi
from .toolkit import build_llm_toolkit, build_lending_pool_toolkit
from .wallet_connect_component import connect_wallet, wallet_command
from .wallet import DEFAULT_SESSION_KEY


def _st_rerun() -> None:
    rerun = getattr(st, "rerun", None)
    if callable(rerun):
        rerun()
    else:
        legacy = getattr(st, "experimental_rerun", None)
        if callable(legacy):
            legacy()


def _render_wallet_section(mm_state: Dict[str, Any], w3: Web3, key_prefix: str, selected: str) -> None:
    mm_payload = mm_state.get("metamask", {})
    tx_req = mm_payload.get("tx_request")
    if isinstance(tx_req, str):
        try:
            tx_req = json.loads(tx_req)
        except json.JSONDecodeError:
            st.warning("Tool provided tx_request that is not valid JSON.")
            tx_req = None
    action = mm_payload.get("action") or "eth_sendTransaction"
    from_address = mm_payload.get("from")
    chain_id = mm_payload.get("chainId")
    if chain_id is None:
        st.warning("Chain ID not provided by tool; ensure your wallet is connected to the correct network.")

    cached = st.session_state.get(DEFAULT_SESSION_KEY, {})
    preferred_address = cached.get("address") if isinstance(cached, dict) else None
    if from_address:
        preferred_address = from_address
        mm_state.setdefault("wallet_address", from_address)

    mm_state.setdefault("pending_command", None)
    mm_state.setdefault("last_result", None)
    mm_state.setdefault("last_value", None)

    pending = mm_state.get("pending_command")
    component_key = f"wallet_headless_{key_prefix}_{selected}"
    command = pending.get("command") if isinstance(pending, dict) else None
    command_payload = pending.get("payload") if isinstance(pending, dict) else None
    command_sequence = pending.get("sequence") if isinstance(pending, dict) else None

    # Invoke the headless bridge component (returns last payload or command result)
    command_payload = {"tx_request": tx_req, "action": action}
    if from_address:
        command_payload["from"] = from_address

    component_value = wallet_command(
        key=component_key,
        command=command,
        command_payload=command_payload,
        command_sequence=command_sequence,
        require_chain_id=chain_id,
        tx_request=tx_req,
        action=action,
        preferred_address=preferred_address,
        autoconnect=True,
    )

    if component_value is not None:
        mm_state["last_value"] = component_value
        if (
            isinstance(pending, dict)
            and isinstance(component_value, dict)
            and component_value.get("commandSequence") == pending.get("sequence")
        ):
            mm_state["last_result"] = component_value
            mm_state["pending_command"] = None
            # Update cached wallet info if provided
            addr = component_value.get("address")
            if addr:
                mm_state["wallet_address"] = addr
            chain = component_value.get("chainId")
            if chain:
                mm_state["wallet_chain"] = chain

    status_cols = st.columns(2)
    with status_cols[0]:
        wallet_addr = mm_state.get("wallet_address") or preferred_address
        if wallet_addr:
            st.info(f"Cached wallet: {wallet_addr}")
        else:
            st.info("No wallet connected yet.")
    with status_cols[1]:
        if chain_id:
            st.info(f"Required chain: {chain_id}")
        if from_address:
            st.caption(f"Requested signer: {from_address}")

    if pending:
        st.warning("Command sent to MetaMask. Confirm in your wallet …")

    btn_cols = st.columns(3)
    if btn_cols[0].button("Connect wallet", key=f"btn_connect_{key_prefix}_{selected}"):
        mm_state["pending_command"] = {
            "command": "connect",
            "payload": {},
            "sequence": int(time() * 1000),
        }
        st.session_state[f"mm_state_{key_prefix}_{selected}"] = mm_state
        _st_rerun()

    if btn_cols[1].button("Switch network", key=f"btn_switch_{key_prefix}_{selected}"):
        mm_state["pending_command"] = {
            "command": "switch_network",
            "payload": {"require_chain_id": chain_id},
            "sequence": int(time() * 1000),
        }
        st.session_state[f"mm_state_{key_prefix}_{selected}"] = mm_state
        _st_rerun()

    send_disabled = tx_req is None
    if btn_cols[2].button("Send transaction", key=f"btn_send_{key_prefix}_{selected}", disabled=send_disabled):
        mm_state["pending_command"] = {
            "command": "send_transaction",
            "payload": {"tx_request": tx_req, "action": action},
            "sequence": int(time() * 1000),
        }
        st.session_state[f"mm_state_{key_prefix}_{selected}"] = mm_state
        _st_rerun()

    last_result = mm_state.get("last_result")
    if isinstance(last_result, dict):
        tx_hash = last_result.get("txHash")
        error_msg = last_result.get("error")
        status = last_result.get("status")
        addr_for_session = last_result.get("address") or mm_state.get("wallet_address")
        chain_for_session = last_result.get("chainId") or mm_state.get("wallet_chain")
        if addr_for_session:
            st.session_state.setdefault(DEFAULT_SESSION_KEY, {})
            if isinstance(st.session_state[DEFAULT_SESSION_KEY], dict):
                st.session_state[DEFAULT_SESSION_KEY]["address"] = addr_for_session
        if chain_for_session:
            st.session_state.setdefault(DEFAULT_SESSION_KEY, {})
            if isinstance(st.session_state[DEFAULT_SESSION_KEY], dict):
                st.session_state[DEFAULT_SESSION_KEY]["chainId"] = chain_for_session
        if error_msg:
            st.error(f"MetaMask command failed: {error_msg}")
        else:
            if status:
                st.success(f"MetaMask status: {status}")
            if tx_hash:
                st.success(f"Transaction sent: {tx_hash}")
                explorer_url = f"https://testnet.arcscan.app/tx/{tx_hash}"
                st.markdown(f"[View on Arcscan]({explorer_url})", help="Opens Arcscan for the transaction")
                with st.spinner("Waiting for receipt…"):
                    try:
                        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
                        st.caption("Transaction receipt")
                        st.json(
                            {
                                "transactionHash": receipt.get("transactionHash").hex()
                                if receipt.get("transactionHash")
                                else tx_hash,
                                "status": receipt.get("status"),
                                "blockNumber": receipt.get("blockNumber"),
                                "gasUsed": receipt.get("gasUsed"),
                                "cumulativeGasUsed": receipt.get("cumulativeGasUsed"),
                            }
                        )
                    except Exception as exc:
                        st.warning(f"Unable to fetch receipt yet: {exc}")

    with st.expander("Transaction request", expanded=False):
        if tx_req is not None:
            st.json(tx_req)
        else:
            st.write("(none)")

    with st.expander("Latest component payload", expanded=False):
        st.write(component_value)

    if st.button("Clear MetaMask state", key=f"clear_mm_{key_prefix}_{selected}"):
        st.session_state.pop(f"mm_state_{key_prefix}_{selected}", None)
        _st_rerun()


def _render_tool_runner(
    tools_schema: list[Dict[str, Any]],
    function_map: Dict[str, Callable[..., str]],
    w3: Web3,
    key_prefix: str,
    parameter_defaults: Dict[str, Dict[str, Any]] | None = None,
) -> None:
    st.subheader("Run a tool")

    if not tools_schema:
        st.info("No MCP tools available. Check contract addresses and ABI paths.")
        return

    tool_names = [entry["function"]["name"] for entry in tools_schema]
    selected = st.selectbox("Choose a tool", tool_names, key=f"{key_prefix}_tool_select")

    # If we already have a pending MetaMask state for this tool, render it first
    mm_state_key = f"mm_state_{key_prefix}_{selected}"
    existing_state = st.session_state.get(mm_state_key)
    if isinstance(existing_state, dict) and existing_state.get("metamask"):
        st.markdown("### MetaMask bridge")
        _render_wallet_section(existing_state, w3, key_prefix, selected)
        st.info("Complete the wallet action above or clear the MetaMask state before running the tool again.")
        return

    schema = next(item for item in tools_schema if item["function"]["name"] == selected)
    parameters = schema["function"].get("parameters", {})
    props = parameters.get("properties", {})
    required = set(parameters.get("required", []))

    inputs: Dict[str, Any] = {}
    for name, details in props.items():
        field_type = details.get("type", "string")
        label = f"{name} ({field_type})"
        default = details.get("default")
        if parameter_defaults:
            default = parameter_defaults.get(selected, {}).get(name, default)

        if field_type == "integer":
            value = st.number_input(label, value=int(default or 0), step=1, key=f"{key_prefix}_param_{selected}_{name}")
            inputs[name] = int(value)
        elif field_type == "number":
            value = st.number_input(label, value=float(default or 0), key=f"{key_prefix}_param_{selected}_{name}")
            inputs[name] = float(value)
        elif field_type == "boolean":
            inputs[name] = st.checkbox(label, value=bool(default) if default is not None else False, key=f"{key_prefix}_param_{selected}_{name}")
        elif field_type == "array":
            raw = st.text_area(
                f"{label} (comma separated)",
                value=", ".join(default or []) if isinstance(default, list) else "",
                key=f"{key_prefix}_param_{selected}_{name}"
            )
            inputs[name] = [item.strip() for item in raw.split(",") if item.strip()]
        else:
            inputs[name] = st.text_input(
                label,
                value=str(default) if default is not None else "",
                key=f"{key_prefix}_param_{selected}_{name}"
            )

    if st.button("Run MCP tool", key=f"{key_prefix}_run_tool"):
        missing = [param for param in required if not inputs.get(param)]
        if missing:
            st.error(f"Missing required parameters: {', '.join(missing)}")
            return

        handler = function_map.get(selected)
        if handler is None:
            st.error("Selected tool does not have an implementation.")
            return

        with st.spinner(f"Running `{selected}`..."):
            try:
                result = handler(**inputs)
            except TypeError as exc:
                st.error(f"Parameter mismatch: {exc}")
                return
            except Exception as exc:
                st.error(f"Tool execution failed: {exc}")
                return

        st.success("Tool completed")
        try:
            parsed = json.loads(result) if isinstance(result, str) else result
        except Exception:
            parsed = result if isinstance(result, str) else json.dumps(result)

        # If the tool produced a MetaMask transaction request, render the wallet component to send it
        if isinstance(parsed, dict) and parsed.get("success") and isinstance(parsed.get("metamask"), dict):
            mm = parsed["metamask"]
            state_key = f"mm_state_{key_prefix}_{selected}"
            mm_state = st.session_state.get(state_key, {}) if isinstance(st.session_state.get(state_key), dict) else {}
            mm_state["metamask"] = mm
            st.session_state[state_key] = mm_state
            st.markdown("### MetaMask bridge")
            _render_wallet_section(mm_state, w3, key_prefix, selected)
            st.stop()

        # Default: show parsed output
        try:
            if isinstance(parsed, (list, dict)):
                st.json(parsed)
            else:
                st.write(parsed)
        except Exception:
            st.write(result if isinstance(result, str) else json.dumps(result))


def render_mcp_tools_page() -> None:
    st.title("🧪 Direct MCP Tool Tester")
    st.caption("Run MCP tools for TrustMintSBT and LendingPool.")

    # Env config
    rpc_url = os.getenv(ARC_RPC_ENV)
    private_key_env = os.getenv(PRIVATE_KEY_ENV)
    default_gas_limit = int(os.getenv(GAS_LIMIT_ENV, "200000"))
    gas_price_gwei = os.getenv(GAS_PRICE_GWEI_ENV, "1")

    # Build web3 client early for chain id and status
    w3 = get_web3_client(rpc_url)

    # Signing role selector (without editing .env)
    st.divider()
    st.subheader("MetaMask Role Assignment")

    chain_id = None
    try:
        chain_id = w3.eth.chain_id if w3 else None
    except Exception:
        chain_id = None

    roles_key = "role_addresses"
    role_addresses: Dict[str, str] = st.session_state.get(roles_key, {}) if isinstance(st.session_state.get(roles_key), dict) else {}
    role_addresses.setdefault("Owner", "")
    role_addresses.setdefault("Lender", "")
    role_addresses.setdefault("Borrower", "")

    wallet_info = connect_wallet(
        key="role_assignment_wallet",
        require_chain_id=chain_id,
        preferred_address=role_addresses.get("Owner") or role_addresses.get("Lender") or role_addresses.get("Borrower"),
        autoconnect=True,
    )

    current_address = wallet_info.get("address") if isinstance(wallet_info, dict) else None
    assignment_col, info_col = st.columns([2, 1])

    with assignment_col:
        role_choice = st.selectbox("Assign connected wallet to role", ["Owner", "Lender", "Borrower"], key="role_assignment_choice")
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

    st.divider()
    st.subheader("Active Role")
    role = st.selectbox("Select role for available tools", ["Read-only", "Owner", "Lender", "Borrower"], index=0, key="signing_role")

    owner_pk = os.getenv(PRIVATE_KEY_ENV)
    lender_pk = os.getenv("LENDER_PRIVATE_KEY")
    borrower_pk = os.getenv("BORROWER_PRIVATE_KEY")

    effective_private_key = None
    if role == "Owner":
        effective_private_key = owner_pk or None
    elif role == "Lender":
        effective_private_key = lender_pk or None
    elif role == "Borrower":
        effective_private_key = borrower_pk or None

    role_private_keys = {
        "Owner": owner_pk or None,
        "Lender": lender_pk or None,
        "Borrower": borrower_pk or None,
    }

    # Status on RPC connectivity and role readiness
    status_col, _, _ = st.columns([2, 0.2, 2])
    with status_col:
        if w3:
            st.success(f"Connected to Arc RPC: {rpc_url}")
        else:
            st.error("RPC unavailable. Set `ARC_TESTNET_RPC_URL` in `.env` and ensure the endpoint is reachable.")
        if role == "Read-only":
            st.info("Read-only mode selected; transactions are disabled.")
        elif role != "Read-only" and not (effective_private_key or role_addresses.get(role)):
            st.warning(
                f"No signer configured for {role}. Assign a MetaMask wallet above or set environment private keys."
            )
    if not w3:
        st.stop()

    st.divider()
    st.header("TrustMint SBT Tools")

    sbt_address, _ = get_sbt_address()
    sbt_abi_path = os.getenv(TRUSTMINT_SBT_ABI_PATH_ENV)
    if not sbt_address or not sbt_abi_path:
        st.warning("Set `SBT_ADDRESS` and `TRUSTMINT_SBT_ABI_PATH` in `.env` to enable SBT tools.")
    else:
        abi = load_contract_abi(sbt_abi_path)
        try:
            sbt_contract: Contract = w3.eth.contract(address=Web3.to_checksum_address(sbt_address), abi=abi)  # type: ignore[arg-type]
            tools_schema, function_map = build_llm_toolkit(
                w3=w3,
                contract=sbt_contract,
                token_decimals=0,
                private_key=effective_private_key,
                default_gas_limit=default_gas_limit,
                gas_price_gwei=gas_price_gwei,
            )
            _render_tool_runner(tools_schema, function_map, w3, key_prefix="sbt")
        except Exception as exc:
            st.error(f"Unable to build SBT contract instance: {exc}")

    st.divider()
    st.header("LendingPool Tools")

    pool_address = os.getenv(LENDING_POOL_ADDRESS_ENV)
    pool_abi_path = os.getenv(LENDING_POOL_ABI_PATH_ENV)
    usdc_address = os.getenv(USDC_ADDRESS_ENV)
    usdc_abi_path = os.getenv(USDC_ABI_PATH_ENV)
    usdc_decimals = int(os.getenv(USDC_DECIMALS_ENV, "18"))
    if usdc_decimals < 18:
        st.warning(
            "USDC_DECIMALS is set to a value below 18. On Arc the native token uses 18 decimals;"
            " update your .env if deposits/repayments appear too small."
        )

    if not pool_address or not pool_abi_path:
        st.warning("Set `LENDING_POOL_ADDRESS` and `LENDING_POOL_ABI_PATH` in `.env` to enable LendingPool tools.")
        return

    pool_abi = load_contract_abi(pool_abi_path)
    usdc_abi = load_contract_abi(usdc_abi_path) if usdc_abi_path else None

    try:
        pool_contract: Contract = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=pool_abi)  # type: ignore[arg-type]
    except Exception as exc:
        st.error(f"Unable to build LendingPool contract instance: {exc}")
        return

    tools_schema, function_map = build_lending_pool_toolkit(
        w3=w3,
        pool_contract=pool_contract,
        token_decimals=usdc_decimals,
        native_decimals=18,
        private_key=effective_private_key,
        default_gas_limit=default_gas_limit,
        gas_price_gwei=gas_price_gwei,
        role_addresses=role_addresses,
        role_private_keys=role_private_keys,
    )

    read_only_tools = {"availableLiquidity", "lenderBalance", "isBanned"}
    owner_tools = {
        "approveUSDC",
        "withdraw",
        "openLoan",
        "checkDefaultAndBan",
        "setDepositLockSeconds",
        "setTrustMintSbt",
        "setMinScoreToBorrow",
        "unban",
        "getLoan",
    }
    lender_tools = {"approveUSDC", "deposit", "withdraw"}
    borrower_tools = {"approveUSDC", "repay", "getLoan"}

    allowed_names = set(read_only_tools)
    if role == "Owner":
        allowed_names.update(owner_tools)
    if role == "Lender":
        allowed_names.update(lender_tools)
    if role == "Borrower":
        allowed_names.update(borrower_tools)

    filtered_schema = [entry for entry in tools_schema if entry["function"]["name"] in allowed_names]
    filtered_map = {name: fn for name, fn in function_map.items() if name in allowed_names}

    parameter_defaults = {
        "lenderBalance": {"lender_address": role_addresses.get("Lender", "")},
        "getLoan": {"borrower_address": role_addresses.get("Borrower", "")},
        "isBanned": {"borrower_address": role_addresses.get("Borrower", "")},
        "deposit": {"amount": 1.0},
        "withdraw": {"amount": 1.0},
        "repay": {"amount": 1.0},
        "openLoan": {
            "borrower_address": role_addresses.get("Borrower", ""),
            "principal": 1.0,
            "term_seconds": 604800,
        },
        "checkDefaultAndBan": {"borrower_address": role_addresses.get("Borrower", "")},
        "unban": {"borrower_address": role_addresses.get("Borrower", "")},
    }

    if not filtered_schema:
        st.info("No tools available for the selected role.")
    else:
        _render_tool_runner(filtered_schema, filtered_map, w3, key_prefix="pool", parameter_defaults=parameter_defaults)

