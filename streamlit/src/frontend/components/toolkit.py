"""SBT-only MCP toolkit with robust tx handling.

This module exposes:
- render_llm_history, render_tool_message: UI helpers used elsewhere
- build_llm_toolkit: returns only TrustMintSBT tools: hasSbt, getScore, issueScore, revokeScore

Refinements:
- Pending nonces with session-based monotonic bump to avoid duplicate sends
- EIP-1559 fees when supported (with env overrides), legacy gasPrice fallback
- Graceful handling of `already known` and `replacement transaction underpriced`
- Preflights: owner check (when available), hasSbt check for revoke
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

import streamlit as st
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError, Web3Exception

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
    get_sbt_address,
)
from .wallet_connect_component import connect_wallet, wallet_command


# ===== UI helpers (kept for Chatbot and pages) =====

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
            import re
            preview_chars = int(os.getenv("CHAT_PREVIEW_MAX_CHARS", "1000"))
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
                            body = seg[name_end + 1 :].strip()
                        trunc = body[:preview_chars]
                        ellipsis = "…" if len(body) > preview_chars else ""
                        st.markdown(f"**{name}**\n\n{trunc}{ellipsis}")
            else:
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


# ===== Internals: gas/nonce helpers and tx sending =====

def _supports_eip1559(w3: Web3) -> bool:
    try:
        latest = w3.eth.get_block("latest")
        return "baseFeePerGas" in latest and latest["baseFeePerGas"] is not None
    except Exception:
        return False


def _fee_params(w3: Web3, gas_price_gwei: str) -> Dict[str, int]:
    """Return fee params for tx: EIP-1559 when supported; otherwise legacy gasPrice.
    Env overrides (optional):
      - ARC_PRIORITY_FEE_GWEI
      - ARC_MAX_FEE_GWEI
    """
    if _supports_eip1559(w3):
        # EIP-1559
        try:
            latest = w3.eth.get_block("latest")
            base = int(latest["baseFeePerGas"])  # wei
        except Exception:
            base = Web3.to_wei(int(gas_price_gwei), "gwei") // 2  # rough fallback
        prio_gwei = int(os.getenv("ARC_PRIORITY_FEE_GWEI", "1"))
        max_gwei = os.getenv("ARC_MAX_FEE_GWEI")
        prio = Web3.to_wei(prio_gwei, "gwei")
        # max fee: base * 2 + prio (conservative)
        max_fee = base * 2 + prio
        if max_gwei:
            try:
                max_fee = Web3.to_wei(int(max_gwei), "gwei")
            except Exception:
                pass
        return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": prio}
    # Legacy
    return {"gasPrice": Web3.to_wei(int(gas_price_gwei), "gwei")}


def _next_nonce(w3: Web3, addr: str) -> int:
    """Pending nonce + session monotonic bump to avoid duplicates on fast clicks."""
    try:
        pending = w3.eth.get_transaction_count(addr, "pending")
    except Exception:
        pending = w3.eth.get_transaction_count(addr)
    key = f"_nonce_{addr.lower()}"
    last = st.session_state.get(key)
    if isinstance(last, int) and pending <= last:
        pending = last + 1
    st.session_state[key] = pending
    return pending


def _sign_and_send(w3: Web3, private_key: str, tx: Dict[str, Any]) -> Dict[str, Any]:
    try:
        signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
        raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
        if raw_tx is None:
            return {"error": "Signed transaction missing rawTransaction/raw_transaction"}
        local_hash = Web3.keccak(raw_tx).hex()
        try:
            tx_hash = w3.eth.send_raw_transaction(raw_tx)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            return {"txHash": tx_hash.hex(), "receipt": _format_receipt(receipt)}
        except Web3Exception as exc:
            text = str(exc)
            if "already known" in text:
                return {"txHash": local_hash, "status": "already_known"}
            if "replacement transaction underpriced" in text:
                return {"txHash": local_hash, "status": "underpriced"}
            raise
    except Exception as exc:
        return {"error": f"sign/send error: {exc}"}


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


def _metamask_tx_request(
    contract: Contract,
    fn_name: str,
    args: list[Any],
    value_wei: int = 0,
    from_address: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal eth_sendTransaction request for MetaMask: {to, data, value}."""

    data_hex: str
    try:
        data_hex = contract.encodeABI(fn_name, args=args)
    except Exception:
        fn = getattr(contract.functions, fn_name)(*args)
        data_hex = fn._encode_transaction_data()  # type: ignore[attr-defined]
    req: Dict[str, Any] = {"to": contract.address, "data": data_hex}
    if value_wei:
        req["value"] = hex(value_wei)
    if from_address:
        try:
            req["from"] = Web3.to_checksum_address(from_address)
        except Exception:
            req["from"] = from_address
    return req


# ===== Public entry: build SBT-only tools =====

def build_llm_toolkit(
    *,
    w3: Web3,
    contract: Contract,
    token_decimals: int,  # unused but preserved for compat
    private_key: Optional[str],
    default_gas_limit: int,
    gas_price_gwei: str,
) -> Tuple[list[Dict[str, Any]], Dict[str, Callable[..., str]]]:
    derived_private_key = private_key or os.getenv(PRIVATE_KEY_ENV)
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

    # ---- Reads ----
    def hasSbt_tool(wallet_address: str) -> str:
        try:
            checksum_wallet = Web3.to_checksum_address(wallet_address)
        except ValueError:
            return tool_error("Invalid wallet address supplied.")
        # Preferred
        try:
            has_fn = getattr(contract.functions, "hasSbt", None)
            if has_fn is not None:
                has = bool(has_fn(checksum_wallet).call())
                return tool_success({"wallet": checksum_wallet, "hasSbt": has, "strategy": "hasSbt"})
        except (ContractLogicError, Web3Exception):
            pass
        # Fallback via ownerOf(tokenId)
        try:
            tid_fn = getattr(contract.functions, "tokenIdOf", None)
            tid = int(tid_fn(checksum_wallet).call()) if tid_fn else int(checksum_wallet, 16)
            owner_of_fn = getattr(contract.functions, "ownerOf", None)
            if owner_of_fn is None:
                fb = w3.eth.contract(
                    address=contract.address,
                    abi=[
                        {
                            "name": "ownerOf",
                            "type": "function",
                            "stateMutability": "view",
                            "inputs": [{"name": "tokenId", "type": "uint256"}],
                            "outputs": [{"name": "", "type": "address"}],
                        }
                    ],
                )
                owner = fb.functions.ownerOf(tid).call()
            else:
                owner = owner_of_fn(tid).call()
            has = owner not in (None, "0x0000000000000000000000000000000000000000")
            return tool_success({"wallet": checksum_wallet, "hasSbt": has, "strategy": "ownerOf_fallback", "tokenId": str(tid), "owner": owner})
        except ContractLogicError:
            return tool_success({"wallet": checksum_wallet, "hasSbt": False, "strategy": "ownerOf_revert"})
        except Web3Exception as exc:
            return tool_error(f"Web3 error: {exc}")
        except Exception as exc:
            return tool_error(f"Unexpected error: {exc}")

    register(
        "hasSbt",
        "Check whether a wallet has a TrustMint SBT.",
        {
            "type": "object",
            "properties": {"wallet_address": {"type": "string", "description": "Wallet address to check."}},
            "required": ["wallet_address"],
        },
        hasSbt_tool,
    )

    def getScore_tool(wallet_address: str) -> str:
        try:
            checksum_wallet = Web3.to_checksum_address(wallet_address)
        except ValueError:
            return tool_error("Invalid wallet address supplied.")
        # Preferred getScore
        try:
            score_fn = getattr(contract.functions, "getScore", None)
            if score_fn is not None:
                value, timestamp, valid = score_fn(checksum_wallet).call()
                return tool_success({"wallet": checksum_wallet, "value": int(value), "timestamp": int(timestamp), "valid": bool(valid), "strategy": "getScore"})
        except (ContractLogicError, Web3Exception):
            pass
        # Fallback scores mapping
        try:
            scores_fn = getattr(contract.functions, "scores", None)
            if scores_fn is not None:
                value, timestamp, valid = scores_fn(checksum_wallet).call()
                return tool_success({"wallet": checksum_wallet, "value": int(value), "timestamp": int(timestamp), "valid": bool(valid), "strategy": "scores"})
        except (ContractLogicError, Web3Exception):
            pass
        # Minimal ABI fallback
        try:
            fb = w3.eth.contract(
                address=contract.address,
                abi=[
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
                        "inputs": [{"name": "", "type": "address"}],
                        "outputs": [
                            {"name": "value", "type": "uint256"},
                            {"name": "timestamp", "type": "uint256"},
                            {"name": "valid", "type": "bool"},
                        ],
                    },
                ],
            )
            try:
                value, timestamp, valid = fb.functions.getScore(checksum_wallet).call()
                strategy = "fallback_getScore"
            except Exception:
                value, timestamp, valid = fb.functions.scores(checksum_wallet).call()
                strategy = "fallback_scores"
            return tool_success({"wallet": checksum_wallet, "value": int(value), "timestamp": int(timestamp), "valid": bool(valid), "strategy": strategy})
        except ContractLogicError as exc:
            return tool_error(f"Contract rejected the call: {exc}")
        except Web3Exception as exc:
            return tool_error(f"Web3 error: {exc}")
        except Exception as exc:
            return tool_error(f"Unexpected error: {exc}")

    register(
        "getScore",
        "Read the TrustMint SBT score tuple (value, timestamp, valid) for a wallet.",
        {
            "type": "object",
            "properties": {"wallet_address": {"type": "string", "description": "Wallet address to query."}},
            "required": ["wallet_address"],
        },
        getScore_tool,
    )

    # ---- Writes ----
    def _preflight_owner(owner_address: str) -> Optional[str]:
        """Return None if OK; otherwise error message."""
        try:
            owner_fn = getattr(contract.functions, "owner", None)
            if owner_fn is None:
                return None
            chain_owner = owner_fn().call()
            if chain_owner.lower() != owner_address.lower():
                return f"PRIVATE_KEY address {owner_address} is not the contract owner {chain_owner}."
            return None
        except Exception:
            return None

    def _preflight_has_sbt(addr: str) -> bool:
        try:
            has_fn = getattr(contract.functions, "hasSbt", None)
            if has_fn is not None:
                return bool(has_fn(addr).call())
        except Exception:
            pass
        # fallback
        try:
            tid = int(getattr(contract.functions, "tokenIdOf", None)(addr).call()) if hasattr(contract.functions, "tokenIdOf") else int(addr, 16)
            owner_fn = getattr(contract.functions, "ownerOf", None)
            if owner_fn is not None:
                o = owner_fn(tid).call()
                return o not in (None, "0x0000000000000000000000000000000000000000")
        except Exception:
            return False
        return False

    def issueScore_tool(wallet_address: str, score_value: int) -> str:
        if not derived_private_key:
            return tool_error("PRIVATE_KEY not configured. Configure it in .env to submit transactions.")
        try:
            checksum_wallet = Web3.to_checksum_address(wallet_address)
        except ValueError:
            return tool_error("Invalid wallet address supplied.")
        try:
            owner_acct = w3.eth.account.from_key(derived_private_key)
        except Exception as exc:
            return tool_error(f"Unable to derive signer from private key: {exc}")
        # Owner check (when available)
        msg = _preflight_owner(owner_acct.address)
        if msg:
            return tool_error(msg)
        try:
            score_value = int(score_value)
            fees = _fee_params(w3, gas_price_gwei)
            nonce = _next_nonce(w3, owner_acct.address)
            fn = getattr(contract.functions, "issueScore", None)
            if fn is None:
                fb = w3.eth.contract(
                    address=contract.address,
                    abi=[
                        {
                            "name": "issueScore",
                            "type": "function",
                            "stateMutability": "nonpayable",
                            "inputs": [
                                {"name": "borrower", "type": "address"},
                                {"name": "value", "type": "uint256"},
                            ],
                            "outputs": [],
                        }
                    ],
                )
                fn = fb.functions.issueScore
            tx = fn(checksum_wallet, score_value).build_transaction(
                {
                    "from": owner_acct.address,
                    "nonce": nonce,
                    "gas": default_gas_limit,
                    "chainId": w3.eth.chain_id,
                    **fees,
                }
            )
            sent = _sign_and_send(w3, derived_private_key, tx)
            if "error" in sent:
                # Retry once with fee bump if underpriced
                if sent.get("status") == "underpriced" or "underpriced" in sent.get("error", ""):
                    # bump fees ~15%
                    if "maxFeePerGas" in fees:
                        fees_bumped = {
                            "maxFeePerGas": int(fees["maxFeePerGas"] * 1.15),
                            "maxPriorityFeePerGas": int(fees["maxPriorityFeePerGas"] * 1.15),
                        }
                    else:
                        fees_bumped = {"gasPrice": int(fees["gasPrice"] * 1.15)}
                    tx["nonce"] = nonce  # same nonce to replace
                    for k, v in fees_bumped.items():
                        tx[k] = v
                    sent = _sign_and_send(w3, derived_private_key, tx)
                if "error" in sent:
                    return tool_error(sent["error"]) if isinstance(sent["error"], str) else tool_error(str(sent["error"]))
            return tool_success(sent)
        except ContractLogicError as exc:
            return tool_error(f"Contract rejected the transaction: {exc}")
        except Web3Exception as exc:
            return tool_error(f"Web3 error: {exc}")
        except Exception as exc:
            return tool_error(f"Unexpected error: {exc}")

    register(
        "issueScore",
        "Issue or update a TrustMint SBT credit score (owner-only).",
        {
            "type": "object",
            "properties": {
                "wallet_address": {"type": "string", "description": "Wallet address to score."},
                "score_value": {"type": "integer", "description": "Numerical credit score to assign."},
            },
            "required": ["wallet_address", "score_value"],
        },
        issueScore_tool,
    )

    def revokeScore_tool(wallet_address: str) -> str:
        if not derived_private_key:
            return tool_error("PRIVATE_KEY not configured. Configure it in .env to submit transactions.")
        try:
            checksum_wallet = Web3.to_checksum_address(wallet_address)
        except ValueError:
            return tool_error("Invalid wallet address supplied.")
        try:
            owner_acct = w3.eth.account.from_key(derived_private_key)
        except Exception as exc:
            return tool_error(f"Unable to derive signer from private key: {exc}")
        # Owner check (when available)
        msg = _preflight_owner(owner_acct.address)
        if msg:
            return tool_error(msg)
        # Preflight: ensure SBT is minted to avoid revert
        if not _preflight_has_sbt(checksum_wallet):
            return tool_error("SBT not minted for this wallet; revokeScore would revert.")
        try:
            fees = _fee_params(w3, gas_price_gwei)
            nonce = _next_nonce(w3, owner_acct.address)
            fn = getattr(contract.functions, "revokeScore", None)
            if fn is None:
                fb = w3.eth.contract(
                    address=contract.address,
                    abi=[
                        {
                            "name": "revokeScore",
                            "type": "function",
                            "stateMutability": "nonpayable",
                            "inputs": [{"name": "borrower", "type": "address"}],
                            "outputs": [],
                        }
                    ],
                )
                fn = fb.functions.revokeScore
            tx = fn(checksum_wallet).build_transaction(
                {
                    "from": owner_acct.address,
                    "nonce": nonce,
                    "gas": default_gas_limit,
                    "chainId": w3.eth.chain_id,
                    **fees,
                }
            )
            sent = _sign_and_send(w3, derived_private_key, tx)
            if "error" in sent:
                # Retry once with fee bump if underpriced
                if sent.get("status") == "underpriced" or "underpriced" in sent.get("error", ""):
                    if "maxFeePerGas" in fees:
                        fees_bumped = {
                            "maxFeePerGas": int(fees["maxFeePerGas"] * 1.15),
                            "maxPriorityFeePerGas": int(fees["maxPriorityFeePerGas"] * 1.15),
                        }
                    else:
                        fees_bumped = {"gasPrice": int(fees["gasPrice"] * 1.15)}
                    tx["nonce"] = nonce
                    for k, v in fees_bumped.items():
                        tx[k] = v
                    sent = _sign_and_send(w3, derived_private_key, tx)
                if "error" in sent:
                    return tool_error(sent["error"]) if isinstance(sent["error"], str) else tool_error(str(sent["error"]))
            return tool_success(sent)
        except ContractLogicError as exc:
            return tool_error(f"Contract rejected the transaction: {exc}")
        except Web3Exception as exc:
            return tool_error(f"Web3 error: {exc}")
        except Exception as exc:
            return tool_error(f"Unexpected error: {exc}")

    register(
        "revokeScore",
        "Revoke (invalidate) an SBT borrower score (owner-only).",
        {
            "type": "object",
            "properties": {"wallet_address": {"type": "string", "description": "Borrower wallet address."}},
            "required": ["wallet_address"],
        },
        revokeScore_tool,
    )

    return tools, handlers

# ===== LendingPool MCP toolkit =====

def build_lending_pool_toolkit(
    *,
    w3: Web3,
    pool_contract: Contract,
    token_decimals: int,
    native_decimals: int,
    private_key: Optional[str],
    default_gas_limit: int,
    gas_price_gwei: str,
    role_addresses: Optional[Dict[str, str]] = None,
    role_private_keys: Optional[Dict[str, Optional[str]]] = None,
) -> Tuple[list[Dict[str, Any]], Dict[str, Callable[..., str]]]:
    tools: list[Dict[str, Any]] = []
    handlers: Dict[str, Callable[..., str]] = {}

    derived_private_key = private_key or os.getenv(PRIVATE_KEY_ENV)
    role_private_keys = role_private_keys or {}
    role_addresses = role_addresses or {}

    def _acct_for_key(key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        try:
            return w3.eth.account.from_key(key).address  # type: ignore[arg-type]
        except Exception:
            return None

    owner_key = role_private_keys.get("Owner") or derived_private_key
    lender_key = role_private_keys.get("Lender") or None
    borrower_key = role_private_keys.get("Borrower") or None

    owner_address = role_addresses.get("Owner") or _acct_for_key(owner_key)
    lender_address = role_addresses.get("Lender") or _acct_for_key(lender_key)
    borrower_address = role_addresses.get("Borrower") or _acct_for_key(borrower_key)

    def register(name: str, description: str, parameters: Dict[str, Any], handler: Callable[..., str]) -> None:
        tools.append({"type": "function", "function": {"name": name, "description": description, "parameters": parameters}})
        handlers[name] = handler

    # Helpers
    def _metamask_success(tx_req: Dict[str, Any], hint: str, from_addr: Optional[str]) -> str:
        payload: Dict[str, Any] = {
            "metamask": {
                "tx_request": tx_req,
                "action": "eth_sendTransaction",
                "chainId": w3.eth.chain_id,
                "hint": hint,
            }
        }
        if from_addr:
            payload["metamask"]["from"] = from_addr
        return tool_success(payload)

    def _fees() -> Dict[str, int]:
        return _fee_params(w3, gas_price_gwei)

    def _to_token_units(amount: float | int, *, use_native: bool = False) -> int:
        try:
            amt = Decimal(str(amount))
            scale = Decimal(10) ** int(native_decimals if use_native else token_decimals)
            return int(amt * scale)
        except Exception:
            return int(amount)

    # ---- Views ----
    def availableLiquidity_tool() -> str:
        try:
            amount = int(getattr(pool_contract.functions, "availableLiquidity")().call())
            return tool_success({"availableLiquidity": amount})
        except Exception as exc:
            return tool_error(f"Read failed: {exc}")

    register(
        "availableLiquidity",
        "Read pool's available liquidity (token balance).",
        {"type": "object", "properties": {}, "required": []},
        lambda: availableLiquidity_tool(),
    )

    def lenderBalance_tool(lender_address: str) -> str:
        try:
            lender = Web3.to_checksum_address(lender_address)
            amount = int(getattr(pool_contract.functions, "lenderBalance")(lender).call())
            return tool_success({"lender": lender, "balance": amount})
        except Exception as exc:
            return tool_error(f"Read failed: {exc}")

    register(
        "lenderBalance",
        "Read net balance (deposits - withdrawals) for a lender.",
        {
            "type": "object",
            "properties": {"lender_address": {"type": "string", "description": "Lender wallet address."}},
            "required": ["lender_address"],
        },
        lenderBalance_tool,
    )

    def getLoan_tool(borrower_address: str) -> str:
        try:
            borrower = Web3.to_checksum_address(borrower_address)
            loan = getattr(pool_contract.functions, "getLoan")(borrower).call()
            principal, outstanding, startTime, dueTime, active = loan
            return tool_success(
                {
                    "borrower": borrower,
                    "principal": int(principal),
                    "outstanding": int(outstanding),
                    "startTime": int(startTime),
                    "dueTime": int(dueTime),
                    "active": bool(active),
                }
            )
        except Exception as exc:
            return tool_error(f"Read failed: {exc}")

    register(
        "getLoan",
        "Read loan struct for a borrower (principal, outstanding, startTime, dueTime, active).",
        {
            "type": "object",
            "properties": {"borrower_address": {"type": "string", "description": "Borrower wallet address."}},
            "required": ["borrower_address"],
        },
        getLoan_tool,
    )

    def isBanned_tool(borrower_address: str) -> str:
        try:
            borrower = Web3.to_checksum_address(borrower_address)
            banned = bool(getattr(pool_contract.functions, "isBanned")(borrower).call())
            return tool_success({"borrower": borrower, "banned": banned})
        except Exception as exc:
            return tool_error(f"Read failed: {exc}")

    register(
        "isBanned",
        "Check if a borrower is banned due to default.",
        {
            "type": "object",
            "properties": {"borrower_address": {"type": "string", "description": "Borrower wallet address."}},
            "required": ["borrower_address"],
        },
        isBanned_tool,
    )

    # ---- Writes ----
    def deposit_tool(amount: float | int) -> str:
        try:
            amt_decimal = Decimal(str(amount))
        except Exception:
            return tool_error("Invalid amount supplied; enter a numeric value.")
        if amt_decimal > Decimal("1"):
            return tool_error("Amount exceeds 1 USDC test limit for safety.")
        try:
            amt = _to_token_units(amt_decimal, use_native=True)
        except Exception as exc:
            return tool_error(f"Invalid amount: {exc}")

        signer = _acct_for_key(lender_key)
        if signer and lender_key:
            try:
                tx = pool_contract.functions.deposit(amt).build_transaction(
                    {
                        "from": signer,
                        "nonce": _next_nonce(w3, signer),
                        "gas": default_gas_limit,
                        "chainId": w3.eth.chain_id,
                        "value": amt,
                        **_fees(),
                    }
                )
                sent = _sign_and_send(w3, lender_key, tx)  # type: ignore[arg-type]
                return tool_success(sent) if "error" not in sent else tool_error(sent.get("error", "deposit failed"))
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected: {exc}")
            except Exception as exc:
                return tool_error(f"deposit failed: {exc}")

        if lender_address:
            try:
                tx_req = _metamask_tx_request(
                    pool_contract,
                    "deposit",
                    [amt],
                    value_wei=amt,
                    from_address=lender_address,
                )
                return _metamask_success(
                    tx_req,
                    "Use MetaMask (lender wallet) to deposit native USDC into the pool.",
                    lender_address,
                )
            except Exception as exc:
                return tool_error(f"Unable to build MetaMask tx: {exc}")

        return tool_error(
            "Lender wallet not configured. Assign a lender address via MetaMask role assignment or set LENDER_PRIVATE_KEY."
        )

    register(
        "deposit",
        "Deposit USDC into the LendingPool (requires prior approve).",
        {
            "type": "object",
            "properties": {"amount": {"type": "number", "description": "Amount in human units (e.g., 100 USDC)."}},
            "required": ["amount"],
        },
        deposit_tool,
    )

    def withdraw_tool(amount: float | int) -> str:
        try:
            amt_decimal = Decimal(str(amount))
        except Exception:
            return tool_error("Invalid amount supplied; enter a numeric value.")
        if amt_decimal > Decimal("1"):
            return tool_error("Amount exceeds 1 USDC test limit for safety.")
        try:
            amt = _to_token_units(amt_decimal, use_native=True)
        except Exception as exc:
            return tool_error(f"Invalid amount: {exc}")

        signer = _acct_for_key(lender_key)
        if signer and lender_key:
            try:
                tx = pool_contract.functions.withdraw(amt).build_transaction(
                    {
                        "from": signer,
                        "nonce": _next_nonce(w3, signer),
                        "gas": default_gas_limit,
                        "chainId": w3.eth.chain_id,
                        **_fees(),
                    }
                )
                sent = _sign_and_send(w3, lender_key, tx)  # type: ignore[arg-type]
                return tool_success(sent) if "error" not in sent else tool_error(sent.get("error", "withdraw failed"))
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected: {exc}")
            except Exception as exc:
                return tool_error(f"withdraw failed: {exc}")

        if lender_address:
            try:
                tx_req = _metamask_tx_request(
                    pool_contract,
                    "withdraw",
                    [amt],
                    from_address=lender_address,
                )
                return _metamask_success(
                    tx_req,
                    "Use MetaMask (lender wallet) to withdraw unlocked funds.",
                    lender_address,
                )
            except Exception as exc:
                return tool_error(f"Unable to build MetaMask tx: {exc}")

        return tool_error(
            "Lender wallet not configured. Assign a lender address via MetaMask role assignment or set LENDER_PRIVATE_KEY."
        )

    register(
        "withdraw",
        "Withdraw available USDC from the LendingPool (subject to liquidity/locks).",
        {
            "type": "object",
            "properties": {"amount": {"type": "number", "description": "Amount in human units."}},
            "required": ["amount"],
        },
        withdraw_tool,
    )

    def openLoan_tool(borrower_address: str, principal: float | int, term_seconds: int) -> str:
        try:
            borrower = Web3.to_checksum_address(borrower_address)
        except ValueError:
            return tool_error("Invalid borrower address supplied.")
        try:
            principal_decimal = Decimal(str(principal))
        except Exception:
            return tool_error("Invalid principal supplied; enter a numeric value.")
        if principal_decimal > Decimal("1"):
            return tool_error("Principal exceeds 1 USDC test limit for safety.")
        try:
            principal_units = _to_token_units(principal_decimal, use_native=True)
        except Exception as exc:
            return tool_error(f"Invalid principal: {exc}")
        signer = _acct_for_key(owner_key)
        if signer and owner_key:
            try:
                fees = _fees()
                nonce = _next_nonce(w3, signer)
                tx = pool_contract.functions.openLoan(borrower, principal_units, int(term_seconds)).build_transaction(
                    {
                        "from": signer,
                        "nonce": nonce,
                        "gas": default_gas_limit,
                        "chainId": w3.eth.chain_id,
                        **fees,
                    }
                )
                sent = _sign_and_send(w3, owner_key, tx)  # type: ignore[arg-type]
                return tool_success(sent) if "error" not in sent else tool_error(sent.get("error", "openLoan failed"))
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected: {exc}")
            except Exception as exc:
                return tool_error(f"openLoan failed: {exc}")

        if owner_address:
            try:
                tx_req = _metamask_tx_request(
                    pool_contract,
                    "openLoan",
                    [borrower, principal_units, int(term_seconds)],
                    from_address=owner_address,
                )
                return _metamask_success(
                    tx_req,
                    "Use MetaMask (owner wallet) to open a loan.",
                    owner_address,
                )
            except Exception as exc:
                return tool_error(f"Unable to build MetaMask tx: {exc}")

        return tool_error(
            "Owner wallet not configured. Assign an owner address via MetaMask role assignment or set PRIVATE_KEY."
        )

    register(
        "openLoan",
        "Owner-only: open a loan for borrower and transfer principal.",
        {
            "type": "object",
            "properties": {
                "borrower_address": {"type": "string", "description": "Borrower wallet address."},
                "principal": {"type": "number", "description": "Principal in human units (e.g., 50 USDC)."},
                "term_seconds": {"type": "integer", "description": "Loan term in seconds (e.g., 604800 for 7 days)."},
            },
            "required": ["borrower_address", "principal", "term_seconds"],
        },
        openLoan_tool,
    )

    def repay_tool(amount: float | int) -> str:
        try:
            amt_decimal = Decimal(str(amount))
        except Exception:
            return tool_error("Invalid amount supplied; enter a numeric value.")
        if amt_decimal > Decimal("1"):
            return tool_error("Amount exceeds 1 USDC test limit for safety.")
        try:
            amt = _to_token_units(amt_decimal, use_native=True)
        except Exception as exc:
            return tool_error(f"Invalid amount: {exc}")

        signer = _acct_for_key(borrower_key)
        if signer and borrower_key:
            try:
                tx = pool_contract.functions.repay(amt).build_transaction(
                    {
                        "from": signer,
                        "nonce": _next_nonce(w3, signer),
                        "gas": default_gas_limit,
                        "chainId": w3.eth.chain_id,
                        "value": amt,
                        **_fees(),
                    }
                )
                sent = _sign_and_send(w3, borrower_key, tx)  # type: ignore[arg-type]
                return tool_success(sent) if "error" not in sent else tool_error(sent.get("error", "repay failed"))
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected: {exc}")
            except Exception as exc:
                return tool_error(f"repay failed: {exc}")

        if borrower_address:
            try:
                tx_req = _metamask_tx_request(
                    pool_contract,
                    "repay",
                    [amt],
                    value_wei=amt,
                    from_address=borrower_address,
                )
                return _metamask_success(
                    tx_req,
                    "Use MetaMask (borrower wallet) to repay the loan.",
                    borrower_address,
                )
            except Exception as exc:
                return tool_error(f"Unable to build MetaMask tx: {exc}")

        return tool_error(
            "Borrower wallet not configured. Assign a borrower address via MetaMask role assignment or set BORROWER_PRIVATE_KEY."
        )

    register(
        "repay",
        "Borrower: repay part/all of outstanding loan (requires USDC approve).",
        {
            "type": "object",
            "properties": {"amount": {"type": "number", "description": "Repayment amount in human units."}},
            "required": ["amount"],
        },
        repay_tool,
    )

    def checkDefaultAndBan_tool(borrower_address: str) -> str:
        signer = _acct_for_key(owner_key)
        try:
            borrower = Web3.to_checksum_address(borrower_address)
        except ValueError:
            return tool_error("Invalid borrower address supplied.")
        if signer and owner_key:
            try:
                fees = _fees()
                nonce = _next_nonce(w3, signer)
                tx = pool_contract.functions.checkDefaultAndBan(borrower).build_transaction(
                    {
                        "from": signer,
                        "nonce": nonce,
                        "gas": default_gas_limit,
                        "chainId": w3.eth.chain_id,
                        **fees,
                    }
                )
                sent = _sign_and_send(w3, owner_key, tx)  # type: ignore[arg-type]
                return tool_success(sent) if "error" not in sent else tool_error(sent.get("error", "checkDefaultAndBan failed"))
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected: {exc}")
            except Exception as exc:
                return tool_error(f"checkDefaultAndBan failed: {exc}")

        if owner_address:
            try:
                tx_req = _metamask_tx_request(
                    pool_contract,
                    "checkDefaultAndBan",
                    [borrower],
                    from_address=owner_address,
                )
                return _metamask_success(
                    tx_req,
                    "Use MetaMask (owner wallet) to check default and ban overdue borrower.",
                    owner_address,
                )
            except Exception as exc:
                return tool_error(f"Unable to build MetaMask tx: {exc}")

        return tool_error(
            "Owner wallet not configured. Assign an owner address via MetaMask role assignment or set PRIVATE_KEY."
        )

    register(
        "checkDefaultAndBan",
        "Anyone: check if borrower defaulted and ban if overdue.",
        {
            "type": "object",
            "properties": {"borrower_address": {"type": "string", "description": "Borrower wallet address."}},
            "required": ["borrower_address"],
        },
        checkDefaultAndBan_tool,
    )

    def setDepositLockSeconds_tool(seconds: int) -> str:
        signer = _acct_for_key(owner_key)
        if signer and owner_key:
            try:
                tx = pool_contract.functions.setDepositLockSeconds(int(seconds)).build_transaction(
                    {
                        "from": signer,
                        "nonce": _next_nonce(w3, signer),
                        "gas": default_gas_limit,
                        "chainId": w3.eth.chain_id,
                        **_fees(),
                    }
                )
                sent = _sign_and_send(w3, owner_key, tx)  # type: ignore[arg-type]
                return tool_success(sent) if "error" not in sent else tool_error(sent.get("error", "setDepositLockSeconds failed"))
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected: {exc}")
            except Exception as exc:
                return tool_error(f"setDepositLockSeconds failed: {exc}")

        if owner_address:
            try:
                tx_req = _metamask_tx_request(
                    pool_contract,
                    "setDepositLockSeconds",
                    [int(seconds)],
                    from_address=owner_address,
                )
                return _metamask_success(
                    tx_req,
                    "Use MetaMask (owner wallet) to update deposit lock duration.",
                    owner_address,
                )
            except Exception as exc:
                return tool_error(f"Unable to build MetaMask tx: {exc}")

        return tool_error(
            "Owner wallet not configured. Assign an owner address via MetaMask role assignment or set PRIVATE_KEY."
        )

    register(
        "setDepositLockSeconds",
        "Owner-only: set global deposit lock duration in seconds.",
        {
            "type": "object",
            "properties": {"seconds": {"type": "integer", "description": "Lock duration in seconds (0 to disable)."}},
            "required": ["seconds"],
        },
        setDepositLockSeconds_tool,
    )

    def setTrustMintSbt_tool(sbt_address: str) -> str:
        try:
            sbt = Web3.to_checksum_address(sbt_address)
        except ValueError:
            return tool_error("Invalid SBT address supplied.")

        signer = _acct_for_key(owner_key)
        if signer and owner_key:
            try:
                tx = pool_contract.functions.setTrustMintSbt(sbt).build_transaction(
                    {
                        "from": signer,
                        "nonce": _next_nonce(w3, signer),
                        "gas": default_gas_limit,
                        "chainId": w3.eth.chain_id,
                        **_fees(),
                    }
                )
                sent = _sign_and_send(w3, owner_key, tx)  # type: ignore[arg-type]
                return tool_success(sent) if "error" not in sent else tool_error(sent.get("error", "setTrustMintSbt failed"))
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected: {exc}")
            except Exception as exc:
                return tool_error(f"setTrustMintSbt failed: {exc}")

        if owner_address:
            try:
                tx_req = _metamask_tx_request(
                    pool_contract,
                    "setTrustMintSbt",
                    [sbt],
                    from_address=owner_address,
                )
                return _metamask_success(
                    tx_req,
                    "Use MetaMask (owner wallet) to set TrustMint SBT address (0x0 to disable).",
                    owner_address,
                )
            except Exception as exc:
                return tool_error(f"Unable to build MetaMask tx: {exc}")

        return tool_error(
            "Owner wallet not configured. Assign an owner address via MetaMask role assignment or set PRIVATE_KEY."
        )

    register(
        "setTrustMintSbt",
        "Owner-only: set TrustMint SBT address for score gating (0x0 to disable).",
        {
            "type": "object",
            "properties": {"sbt_address": {"type": "string", "description": "SBT contract address or 0x000... to disable."}},
            "required": ["sbt_address"],
        },
        setTrustMintSbt_tool,
    )

    def setMinScoreToBorrow_tool(new_min_score: int) -> str:
        signer = _acct_for_key(owner_key)
        if signer and owner_key:
            try:
                tx = pool_contract.functions.setMinScoreToBorrow(int(new_min_score)).build_transaction(
                    {
                        "from": signer,
                        "nonce": _next_nonce(w3, signer),
                        "gas": default_gas_limit,
                        "chainId": w3.eth.chain_id,
                        **_fees(),
                    }
                )
                sent = _sign_and_send(w3, owner_key, tx)  # type: ignore[arg-type]
                return tool_success(sent) if "error" not in sent else tool_error(sent.get("error", "setMinScoreToBorrow failed"))
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected: {exc}")
            except Exception as exc:
                return tool_error(f"setMinScoreToBorrow failed: {exc}")

        if owner_address:
            try:
                tx_req = _metamask_tx_request(
                    pool_contract,
                    "setMinScoreToBorrow",
                    [int(new_min_score)],
                    from_address=owner_address,
                )
                return _metamask_success(
                    tx_req,
                    "Use MetaMask (owner wallet) to set minimum score for borrowing.",
                    owner_address,
                )
            except Exception as exc:
                return tool_error(f"Unable to build MetaMask tx: {exc}")

        return tool_error(
            "Owner wallet not configured. Assign an owner address via MetaMask role assignment or set PRIVATE_KEY."
        )

    register(
        "setMinScoreToBorrow",
        "Owner-only: set minimum score threshold for borrowing.",
        {
            "type": "object",
            "properties": {"new_min_score": {"type": "integer", "description": "Minimum score required."}},
            "required": ["new_min_score"],
        },
        setMinScoreToBorrow_tool,
    )

    def unban_tool(borrower_address: str) -> str:
        try:
            borrower = Web3.to_checksum_address(borrower_address)
        except ValueError:
            return tool_error("Invalid borrower address supplied.")

        signer = _acct_for_key(owner_key)
        if signer and owner_key:
            try:
                tx = pool_contract.functions.unban(borrower).build_transaction(
                    {
                        "from": signer,
                        "nonce": _next_nonce(w3, signer),
                        "gas": default_gas_limit,
                        "chainId": w3.eth.chain_id,
                        **_fees(),
                    }
                )
                sent = _sign_and_send(w3, owner_key, tx)  # type: ignore[arg-type]
                return tool_success(sent) if "error" not in sent else tool_error(sent.get("error", "unban failed"))
            except ContractLogicError as exc:
                return tool_error(f"Contract rejected: {exc}")
            except Exception as exc:
                return tool_error(f"unban failed: {exc}")

        if owner_address:
            try:
                tx_req = _metamask_tx_request(
                    pool_contract,
                    "unban",
                    [borrower],
                    from_address=owner_address,
                )
                return _metamask_success(
                    tx_req,
                    "Use MetaMask (owner wallet) to unban borrower after remedy.",
                    owner_address,
                )
            except Exception as exc:
                return tool_error(f"Unable to build MetaMask tx: {exc}")

        return tool_error(
            "Owner wallet not configured. Assign an owner address via MetaMask role assignment or set PRIVATE_KEY."
        )

    register(
        "unban",
        "Owner-only: unban a borrower after remedy.",
        {
            "type": "object",
            "properties": {"borrower_address": {"type": "string", "description": "Borrower wallet address."}},
            "required": ["borrower_address"],
        },
        unban_tool,
    )

    return tools, handlers
