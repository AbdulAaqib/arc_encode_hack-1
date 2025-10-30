from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional, Tuple

from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError, Web3Exception

from .messages import tool_success, tool_error
from .tx_helpers import fee_params, next_nonce, sign_and_send

from ..config import PRIVATE_KEY_ENV


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
        tools.append({"type": "function", "function": {"name": name, "description": description, "parameters": parameters}})
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
            fees = fee_params(w3, gas_price_gwei)
            nonce = next_nonce(w3, owner_acct.address)
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
            sent = sign_and_send(w3, derived_private_key, tx)
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
                    sent = sign_and_send(w3, derived_private_key, tx)
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
            fees = fee_params(w3, gas_price_gwei)
            nonce = next_nonce(w3, owner_acct.address)
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
            sent = sign_and_send(w3, derived_private_key, tx)
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
                    sent = sign_and_send(w3, derived_private_key, tx)
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

