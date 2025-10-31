"""MCP toolkit: tool schema builder and helper utilities for tool messages."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

import streamlit as st
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError, Web3Exception


def tool_success(payload: Dict[str, Any]) -> str:
    return json.dumps({"success": True, **payload}, default=_json_default)


def tool_error(message: str, **extras: Any) -> str:
    return json.dumps({"success": False, "error": message, **extras}, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value


def render_tool_message(tool_name: str, content: str) -> None:
    with st.chat_message("assistant"):
        st.markdown(f"**Tool `{tool_name}` output:**")
        _render_tool_content(content)


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


def _render_user_message(content: str) -> None:
    with st.chat_message("user"):
        if content and "[Attached documents]" in content:
            pre, attach_block = content.split("[Attached documents]", 1)
            st.markdown(pre.strip())
            # Truncate preview to avoid overly large UI; configurable via env var
            import os
            import re
            preview_chars = int(os.getenv("CHAT_PREVIEW_MAX_CHARS", "1000"))
            # Parse sections by headers created in attachment context: "### <filename>\n<excerpt>"
            sections = re.split(r"(?m)^###\s*", attach_block)
            if len(sections) > 1:
                with st.expander("Attached documents (truncated preview)"):
                    for seg in sections:
                        seg = seg.strip()
                        if not seg:
                            continue
                        name_end = seg.find("\n")
                        if name_end == -1:
                            name = seg
                            body = ""
                        else:
                            name = seg[:name_end].strip()
                            body = seg[name_end + 1:].strip()
                        trunc = body[:preview_chars]
                        ellipsis = "…" if len(body) > preview_chars else ""
                        st.markdown(f"**{name}**\n\n{trunc}{ellipsis}")
            else:
                # No sections parsed; show label only
                with st.expander("Attached documents"):
                    st.markdown("(preview unavailable)")
        else:
            st.markdown(content or "")


def render_llm_history(messages: Iterable[Dict[str, Any]]) -> None:
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            continue
        if role == "user":
            _render_user_message(content or "")
        elif role == "assistant":
            with st.chat_message("assistant"):
                st.markdown(content or "")
        elif role == "tool":
            render_tool_message(message.get("name", "tool"), content or "")


def build_llm_toolkit(
    *,
    w3: Web3,
    contract: Contract,
    token_decimals: int,
    private_key: Optional[str],
    default_gas_limit: int,
    gas_price_gwei: str,
) -> Tuple[list[Dict[str, Any]], Dict[str, Callable[..., str]]]:
    tools: list[Dict[str, Any]] = []
    handlers: Dict[str, Callable[..., str]] = {}

    def register(
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable[..., str],
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

    def wallet_balance_tool(wallet_address: str) -> str:
        try:
            checksum_wallet = Web3.to_checksum_address(wallet_address)
            balance_wei = w3.eth.get_balance(checksum_wallet)
            balance_native = Web3.from_wei(balance_wei, "ether")
            return tool_success(
                {
                    "wallet": checksum_wallet,
                    "balanceWei": str(balance_wei),
                    "balanceNative": str(balance_native),
                    "unit": "ETH",
                }
            )
        except ValueError:
            return tool_error("Invalid wallet address supplied.")
        except Web3Exception as exc:
            return tool_error(f"Web3 error: {exc}")
        except Exception as exc:
            return tool_error(f"Unexpected error: {exc}")

    register(
        "wallet_balance",
        "Fetch the native token balance for a wallet on Arc.",
        {
            "type": "object",
            "properties": {
                "wallet_address": {
                    "type": "string",
                    "description": "Wallet address to inspect (0x…).",
                }
            },
            "required": ["wallet_address"],
        },
        wallet_balance_tool,
    )

    def available_credit_tool(wallet_address: str) -> str:
        available_credit_fn = getattr(contract.functions, "availableCredit", None)
        if available_credit_fn is None:
            return tool_error("The configured contract does not expose `availableCredit`.")
        try:
            checksum_wallet = Web3.to_checksum_address(wallet_address)
            raw_amount = available_credit_fn(checksum_wallet).call()
            human = raw_amount / (10**token_decimals)
            return tool_success(
                {
                    "wallet": checksum_wallet,
                    "availableCreditRaw": str(raw_amount),
                    "availableCredit": human,
                    "tokenDecimals": token_decimals,
                }
            )
        except ValueError:
            return tool_error("Invalid wallet address supplied.")
        except ContractLogicError as exc:
            return tool_error(f"Contract rejected the call: {exc}")
        except Web3Exception as exc:
            return tool_error(f"Web3 error: {exc}")
        except Exception as exc:
            return tool_error(f"Unexpected error: {exc}")

    register(
        "available_credit",
        "Read the available credit for a borrower from the configured contract.",
        {
            "type": "object",
            "properties": {
                "wallet_address": {
                    "type": "string",
                    "description": "Borrower wallet address to query.",
                }
            },
            "required": ["wallet_address"],
        },
        available_credit_tool,
    )

    def credit_score_tool(wallet_address: str) -> str:
        score_fn = getattr(contract.functions, "getScore", None)
        if score_fn is None:
            score_fn = getattr(contract.functions, "scores", None)
        if score_fn is None:
            # Fallback: build a minimal ABI for registry read methods if the loaded ABI doesn't include them
            try:
                minimal_abi = [
                    {
                        "name": "getScore",
                        "type": "function",
                        "stateMutability": "view",
                        "inputs": [{"name": "borrower", "type": "address"}],
                        "outputs": [
                            {"name": "value", "type": "uint256"},
                            {"name": "timestamp", "type": "uint256"},
                            {"name": "valid", "type": "bool"},
                        ],
                    },
                    {
                        "name": "scores",
                        "type": "function",
                        "stateMutability": "view",
                        "inputs": [{"name": "borrower", "type": "address"}],
                        "outputs": [
                            {"name": "value", "type": "uint256"},
                            {"name": "timestamp", "type": "uint256"},
                            {"name": "valid", "type": "bool"},
                        ],
                    },
                ]
                fallback_contract = w3.eth.contract(address=contract.address, abi=minimal_abi)
                score_fn = getattr(fallback_contract.functions, "getScore", None) or getattr(
                    fallback_contract.functions, "scores", None
                )
            except Exception:
                score_fn = None
        if score_fn is None:
            return tool_error("The configured contract does not expose credit score functions.")
        try:
            checksum_wallet = Web3.to_checksum_address(wallet_address)
            value, timestamp, valid = score_fn(checksum_wallet).call()
            return tool_success(
                {
                    "wallet": checksum_wallet,
                    "score": int(value),
                    "timestamp": int(timestamp),
                    "valid": bool(valid),
                }
            )
        except ValueError:
            return tool_error("Invalid wallet address supplied.")
        except ContractLogicError as exc:
            return tool_error(f"Contract rejected the call: {exc}")
        except Web3Exception as exc:
            return tool_error(f"Web3 error: {exc}")
        except Exception as exc:
            return tool_error(f"Unexpected error: {exc}")

    register(
        "credit_score",
        "Fetch the credit score tuple (value, timestamp, validity) for a borrower.",
        {
            "type": "object",
            "properties": {
                "wallet_address": {
                    "type": "string",
                    "description": "Borrower wallet address to query.",
                }
            },
            "required": ["wallet_address"],
        },
        credit_score_tool,
    )

    if private_key and hasattr(contract.functions, "issueScore"):
        def issue_score_tool(wallet_address: str, score_value: int) -> str:
            try:
                checksum_wallet = Web3.to_checksum_address(wallet_address)
            except ValueError:
                return tool_error("Invalid wallet address supplied.")
            try:
                owner_account = w3.eth.account.from_key(private_key)
            except Exception as exc:
                return tool_error(f"Unable to derive signer from private key: {exc}")
            try:
                gas_price = Web3.to_wei(gas_price_gwei, "gwei")
                nonce = w3.eth.get_transaction_count(owner_account.address)
                tx = contract.functions.issueScore(checksum_wallet, int(score_value)).build_transaction(
                    {
                        "from": owner_account.address,
                        "nonce": nonce,
                        "gas": default_gas_limit,
                        "gasPrice": gas_price,
                        "chainId": w3.eth.chain_id,
                    }
                )
                signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
                tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
                return tool_success({"txHash": tx_hash.hex(), "receipt": _format_receipt(receipt)})
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected the transaction: {exc}")
            except Web3Exception as exc:
                return tool_error(f"Web3 error: {exc}")
            except Exception as exc:
                return tool_error(f"Unexpected error: {exc}")

        register(
            "issue_score",
            "Issue or update a credit score for a borrower (owner only).",
            {
                "type": "object",
                "properties": {
                    "wallet_address": {
                        "type": "string",
                        "description": "Borrower wallet address to score.",
                    },
                    "score_value": {
                        "type": "integer",
                        "description": "Numerical credit score to assign.",
                        "default": 700,
                    },
                },
                "required": ["wallet_address", "score_value"],
            },
            issue_score_tool,
        )

    if private_key and hasattr(contract.functions, "revokeScore"):
        def revoke_score_tool(wallet_address: str) -> str:
            try:
                checksum_wallet = Web3.to_checksum_address(wallet_address)
            except ValueError:
                return tool_error("Invalid wallet address supplied.")
            try:
                owner_account = w3.eth.account.from_key(private_key)
            except Exception as exc:
                return tool_error(f"Unable to derive signer from private key: {exc}")
            try:
                gas_price = Web3.to_wei(gas_price_gwei, "gwei")
                nonce = w3.eth.get_transaction_count(owner_account.address)
                tx = contract.functions.revokeScore(checksum_wallet).build_transaction(
                    {
                        "from": owner_account.address,
                        "nonce": nonce,
                        "gas": default_gas_limit,
                        "gasPrice": gas_price,
                        "chainId": w3.eth.chain_id,
                    }
                )
                signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
                tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
                receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
                return tool_success({"txHash": tx_hash.hex(), "receipt": _format_receipt(receipt)})
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected the transaction: {exc}")
            except Web3Exception as exc:
                return tool_error(f"Web3 error: {exc}")
            except Exception as exc:
                return tool_error(f"Unexpected error: {exc}")

        register(
            "revoke_score",
            "Revoke a previously issued credit score (owner only).",
            {
                "type": "object",
                "properties": {
                    "wallet_address": {
                        "type": "string",
                        "description": "Borrower wallet address to revoke.",
                    }
                },
                "required": ["wallet_address"],
            },
            revoke_score_tool,
        )

    def describe_contract_tool() -> str:
        view_functions: list[Dict[str, Any]] = []
        write_functions: list[Dict[str, Any]] = []
        for item in contract.abi:
            if item.get("type") != "function":
                continue
            signature = {
                "name": item.get("name"),
                "inputs": [f"{arg.get('type')} {arg.get('name')}" for arg in item.get("inputs", [])],
                "outputs": [
                    f"{arg.get('type')} {arg.get('name')}" if arg.get("name") else arg.get("type")
                    for arg in item.get("outputs", [])
                ],
            }
            if item.get("stateMutability") in {"view", "pure"}:
                view_functions.append(signature)
            else:
                write_functions.append(signature)
        return tool_success({"readOnly": view_functions, "stateChanging": write_functions})

    register(
        "describe_contract",
        "Summarize the contract's callable functions exposed in the ABI.",
        {"type": "object", "properties": {}, "required": []},
        describe_contract_tool,
    )

    return tools, handlers


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
