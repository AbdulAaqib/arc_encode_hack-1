"""MCP tools component to interact with Arc smart contracts from Streamlit."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

import streamlit as st
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError, Web3Exception

# New modular imports (robust to missing config module)
try:
    from .config import (
        ARC_RPC_ENV,
        CONTRACT_ABI_PATH_ENV,
        PRIVATE_KEY_ENV,
        USDC_DECIMALS_ENV,
        GAS_LIMIT_ENV,
        GAS_PRICE_GWEI_ENV,
        get_contract_address_with_fallback,
        CREDIT_SCORE_REGISTRY_ABI_PATH_ENV,
    )
except Exception:  # pragma: no cover - fallback when config is unavailable
    ARC_RPC_ENV = "ARC_TESTNET_RPC_URL"
    CONTRACT_ABI_PATH_ENV = "ARC_CREDIT_LINE_MANAGER_ABI_PATH"
    PRIVATE_KEY_ENV = "PRIVATE_KEY"
    USDC_DECIMALS_ENV = "ARC_USDC_DECIMALS"
    GAS_LIMIT_ENV = "ARC_GAS_LIMIT"
    GAS_PRICE_GWEI_ENV = "ARC_GAS_PRICE_GWEI"
    CREDIT_SCORE_REGISTRY_ABI_PATH_ENV = "ARC_CREDIT_LINE_MANAGER_ABI_PATH"

    def get_contract_address_with_fallback() -> tuple[Optional[str], str]:
        addr = os.getenv("CREDIT_LINE_MANAGER_ADDRESS")
        if addr:
            return addr, "CREDIT_LINE_MANAGER_ADDRESS"
        addr = os.getenv("CREDIT_SCORE_REGISTRY_ADDRESS")
        if addr:
            return addr, "CREDIT_SCORE_REGISTRY_ADDRESS"
        return None, ""

from .web3_utils import get_web3_client, load_contract_abi
from .toolkit import build_llm_toolkit, render_llm_history, render_tool_message, tool_error, tool_success


@st.cache_data(show_spinner=False)
def _format_receipt(receipt: Any) -> dict[str, Any]:
    if receipt is None:
        return {"status": "pending"}
    return {
        "transactionHash": receipt["transactionHash"].hex() if receipt.get("transactionHash") else None,
        "status": receipt.get("status"),
        "blockNumber": receipt.get("blockNumber"),
        "gasUsed": receipt.get("gasUsed"),
        "cumulativeGasUsed": receipt.get("cumulativeGasUsed"),
    }


def render_mcp_tools_page() -> None:
    st.title("🛠️ MCP Tools")
    st.caption("Directly call CreditLineManager smart contracts from Streamlit using env-configured credentials.")

    rpc_url = os.getenv(ARC_RPC_ENV)
    contract_address, address_source = get_contract_address_with_fallback()
    abi_env_key = (
        CONTRACT_ABI_PATH_ENV
        if address_source != "CREDIT_SCORE_REGISTRY_ADDRESS"
        else CREDIT_SCORE_REGISTRY_ABI_PATH_ENV
    )
    abi_path = os.getenv(abi_env_key)
    private_key = os.getenv(PRIVATE_KEY_ENV)
    token_decimals = int(os.getenv(USDC_DECIMALS_ENV, "6"))
    default_gas_limit = int(os.getenv(GAS_LIMIT_ENV, "200000"))
    gas_price_gwei = os.getenv(GAS_PRICE_GWEI_ENV, "1")

    w3 = get_web3_client(rpc_url)
    abi = load_contract_abi(abi_path)

    status_col, _, hints_col = st.columns([2, 0.2, 2])
    with status_col:
        if w3:
            st.success(f"Connected to Arc RPC: {rpc_url}")
        else:
            st.error(
                "RPC connection unavailable. Set `ARC_TESTNET_RPC_URL` in `.env` and ensure the endpoint is reachable."
            )
        if not abi:
            st.warning(
                "Contract ABI not found. Point `ARC_CREDIT_LINE_MANAGER_ABI_PATH` to the ABI JSON (use the Registry ABI for credit score tools)."
            )
        if not contract_address:
            st.warning("Set `CREDIT_SCORE_REGISTRY_ADDRESS` in `.env`.")
        if not private_key:
            st.info("`PRIVATE_KEY` not configured. Read-only calls will work, draws will be disabled.")

    

    if not (w3 and abi and contract_address):
        st.stop()

    try:
        contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=abi)
    except Exception as exc:
        st.error(f"Unable to build contract instance: {exc}")
        st.stop()

    has_available_credit = hasattr(contract.functions, "availableCredit")
    has_draw = hasattr(contract.functions, "draw")

    st.divider()
    st.subheader("📊 Read credit availability")
    wallet_address = st.text_input("Borrower wallet", key="mcp_wallet_input")

    if has_available_credit:
        if st.button("Check available credit", type="primary", key="check_credit"):
            if not wallet_address:
                st.error("Enter a wallet address to query.")
            else:
                with st.spinner("Fetching available credit..."):
                    try:
                        checksum_wallet = Web3.to_checksum_address(wallet_address)
                        raw_amount = contract.functions.availableCredit(checksum_wallet).call()
                        human = raw_amount / (10**token_decimals)
                        st.success(f"Available credit: {human:,.2f} USDC")
                    except ValueError:
                        st.error("Wallet address is invalid. Please enter a valid checksum address.")
                    except Web3Exception as exc:  # pragma: no cover - surfaced in UI
                        st.error(f"Web3 error: {exc}")
                    except Exception as exc:  # pragma: no cover - surfaced in UI
                        st.error(f"Unexpected error: {exc}")
    else:
        st.info("The connected contract does not expose `availableCredit`. Configure the CreditLineManager ABI to enable this panel.")

    st.divider()
    st.subheader("💸 Draw funds")
    draw_amount = st.number_input(
        "Draw amount (USDC)", min_value=0.0, step=100.0, format="%0.2f", key="draw_amount_input"
    )

    if has_draw:
        if st.button("Send draw transaction", key="draw_tx_button"):
            if not private_key:
                st.error("Configure `PRIVATE_KEY` in `.env` to submit transactions securely.")
            elif not wallet_address:
                st.error("Enter the borrower wallet to submit a draw.")
            elif draw_amount <= 0:
                st.error("Draw amount must be greater than zero.")
            else:
                with st.spinner("Submitting draw transaction..."):
                    try:
                        checksum_wallet = Web3.to_checksum_address(wallet_address)
                        nonce = w3.eth.get_transaction_count(checksum_wallet)
                        gas_price = Web3.to_wei(gas_price_gwei, "gwei")
                        scaled_amount = int(draw_amount * (10**token_decimals))

                        tx = contract.functions.draw(checksum_wallet, scaled_amount).build_transaction(
                            {
                                "from": checksum_wallet,
                                "nonce": nonce,
                                "gas": default_gas_limit,
                                "gasPrice": gas_price,
                                "chainId": w3.eth.chain_id,
                            }
                        )

                        signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
                        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
                        st.success(f"Transaction sent: {tx_hash.hex()}")

                        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
                        st.json(_format_receipt(receipt))
                    except ValueError as exc:
                        st.error(f"Transaction error: {exc}")
                    except Web3Exception as exc:  # pragma: no cover - surfaced in UI
                        st.error(f"Web3 error: {exc}")
                    except Exception as exc:  # pragma: no cover - surfaced in UI
                        st.error(f"Unexpected error: {exc}")
    else:
        st.info("The connected contract does not expose `draw`. Configure the CreditLineManager ABI to enable transactions.")

    st.info("Use the tool tester below to run MCP helpers directly, or chat with the MCP assistant for guided workflows.")

    tools_schema, function_map = build_llm_toolkit(
        w3=w3,
        contract=contract,
        token_decimals=token_decimals,
        private_key=private_key,
        default_gas_limit=default_gas_limit,
        gas_price_gwei=gas_price_gwei,
    )

    st.divider()
    _render_manual_tool_runner(tools_schema, function_map)

    st.divider()


def _render_manual_tool_runner(
    tools_schema: list[Dict[str, Any]],
    function_map: Dict[str, Callable[..., str]],
) -> None:
    st.subheader("🧪 Direct MCP Tool Tester")

    if not tools_schema:
        st.info("No MCP tools are available to run directly.")
        return

    tool_names = [entry["function"]["name"] for entry in tools_schema]
    selected = st.selectbox("Choose a tool", tool_names, key="manual_tool_select")

    schema = next(item for item in tools_schema if item["function"]["name"] == selected)
    parameters = schema["function"].get("parameters", {})
    props = parameters.get("properties", {})
    required = set(parameters.get("required", []))

    inputs: Dict[str, Any] = {}
    for name, details in props.items():
        field_type = details.get("type", "string")
        label = f"{name} ({field_type})"
        default = details.get("default")

        if field_type == "integer":
            value = st.number_input(label, value=int(default or 0), step=1, key=f"manual_{selected}_{name}")
            inputs[name] = int(value)
        elif field_type == "number":
            value = st.number_input(label, value=float(default or 0), key=f"manual_{selected}_{name}")
            inputs[name] = float(value)
        elif field_type == "boolean":
            inputs[name] = st.checkbox(label, value=bool(default) if default is not None else False, key=f"manual_{selected}_{name}")
        elif field_type == "array":
            raw = st.text_area(
                f"{label} (comma separated)",
                value=", ".join(default or []) if isinstance(default, list) else "",
                key=f"manual_{selected}_{name}"
            )
            inputs[name] = [item.strip() for item in raw.split(",") if item.strip()]
        else:
            inputs[name] = st.text_input(
                label,
                value=str(default) if default is not None else "",
                key=f"manual_{selected}_{name}"
            )

    if st.button("Run MCP tool", key="manual_tool_execute"):
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
            except Exception as exc:  # pragma: no cover - surfaced via UI
                st.error(f"Tool execution failed: {exc}")
                return

        st.success("Tool completed")
        _render_tool_content(result if isinstance(result, str) else tool_success(result))


def _render_tool_content(content: str) -> None:
    if not content:
        st.write("(no content returned)")
        return
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        st.markdown(content)
        return
    if isinstance(parsed, (list, dict)):
        st.json(parsed)
    else:
        st.write(parsed)

