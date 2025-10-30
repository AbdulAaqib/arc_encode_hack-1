from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from web3 import Web3
from web3.contract import Contract
from web3.exceptions import Web3Exception

import streamlit as st

from ..web3_utils import encode_contract_call

_CUSTOM_ERROR_MAP: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "33b2879b": ("DepositAmountZero", ()),
    "1be8a36f": ("DepositValueMismatch", ("uint256", "uint256")),
    "dfef226b": ("DepositAmountTooLarge", ()),
    "96b74521": ("WithdrawAmountZero", ()),
    "2208687a": ("WithdrawExceedsDeposits", ()),
    "a7e374a9": ("DepositEntryDepleted", ()),
    "ce4fdd2a": ("DepositLocked", ("uint256",)),
    "382ea22b": ("PoolLiquidityInsufficient", ("uint256", "uint256")),
    "e83216f3": ("TransferToLenderFailed", ()),
    "fe1889a4": ("BorrowerBannedError", ("address",)),
    "8e05e6a6": ("LoanPrincipalZero", ()),
    "20987137": ("LoanTermZero", ()),
    "a5b4952f": ("BorrowerMissingSbt", ("address",)),
    "9f676696": ("BorrowerScoreInvalid", ("address",)),
    "bcb7870b": ("BorrowerScoreTooLow", ("address", "uint256", "uint256")),
    "968f5518": ("BorrowerHasUnpaidLoan", ("address", "uint256")),
    "8046c066": ("TransferToBorrowerFailed", ()),
    "1e23d144": ("NoActiveLoan", ()),
    "1e8cf24a": ("RepayAmountZero", ()),
    "4a6074de": ("RepayValueMismatch", ("uint256", "uint256")),
    "2ae068e7": ("RepayAmountTooLarge", ("uint256", "uint256")),
    "b8e4806a": ("BorrowerNotBanned", ("address",)),
}


def supports_eip1559(w3: Web3) -> bool:
    try:
        latest = w3.eth.get_block("latest")
        return "baseFeePerGas" in latest and latest["baseFeePerGas"] is not None
    except Exception:
        return False


def fee_params(w3: Web3, gas_price_gwei: str) -> Dict[str, int]:
    """Return fee params for tx: EIP-1559 when supported; otherwise legacy gasPrice.
    Env overrides (optional): ARC_PRIORITY_FEE_GWEI, ARC_MAX_FEE_GWEI
    """
    if supports_eip1559(w3):
        try:
            latest = w3.eth.get_block("latest")
            base = int(latest["baseFeePerGas"])  # wei
        except Exception:
            base = Web3.to_wei(int(gas_price_gwei), "gwei") // 2
        prio_gwei = int(os.getenv("ARC_PRIORITY_FEE_GWEI", "1"))
        max_gwei = os.getenv("ARC_MAX_FEE_GWEI")
        prio = Web3.to_wei(prio_gwei, "gwei")
        max_fee = base * 2 + prio
        if max_gwei:
            try:
                max_fee = Web3.to_wei(int(max_gwei), "gwei")
            except Exception:
                pass
        return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": prio}
    return {"gasPrice": Web3.to_wei(int(gas_price_gwei), "gwei")}


def next_nonce(w3: Web3, addr: str) -> int:
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


def sign_and_send(w3: Web3, private_key: str, tx: Dict[str, Any]) -> Dict[str, Any]:
    try:
        signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
        raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction", None)
        if raw_tx is None:
            return {"error": "Signed transaction missing rawTransaction/raw_transaction"}
        local_hash = Web3.keccak(raw_tx).hex()
        try:
            tx_hash = w3.eth.send_raw_transaction(raw_tx)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            formatted = format_receipt(receipt)
            status = formatted.get("status")
            if status in (1, True):
                return {"txHash": tx_hash.hex(), "receipt": formatted}
            error_payload: Dict[str, Any] = {
                "error": "Transaction reverted",
                "txHash": tx_hash.hex(),
                "receipt": formatted,
            }
            revert_reason = _extract_revert_reason(w3, tx, receipt)
            if revert_reason:
                error_payload["reason"] = revert_reason
            return error_payload
        except Web3Exception as exc:
            text = str(exc)
            if "already known" in text:
                return {"txHash": local_hash, "status": "already_known"}
            if "replacement transaction underpriced" in text:
                return {"txHash": local_hash, "status": "underpriced"}
            raise
    except Exception as exc:
        return {"error": f"sign/send error: {exc}"}


def format_receipt(receipt: Any) -> dict[str, Any]:
    if receipt is None:
        return {"status": "pending"}
    return {
        "transactionHash": receipt["transactionHash"].hex() if receipt.get("transactionHash") else None,
        "status": receipt.get("status"),
        "blockNumber": receipt.get("blockNumber"),
        "gasUsed": receipt.get("gasUsed"),
        "cumulativeGasUsed": receipt.get("cumulativeGasUsed"),
    }


def _extract_revert_reason(w3: Web3, tx: Dict[str, Any], receipt: Any) -> Optional[str]:
    block_number = receipt.get("blockNumber") if isinstance(receipt, dict) else getattr(receipt, "blockNumber", None)
    if block_number is None:
        return None

    try:
        call_tx = dict(tx)
        for key in ("nonce", "gas", "gasPrice", "maxFeePerGas", "maxPriorityFeePerGas"):
            call_tx.pop(key, None)
        call_tx.setdefault("from", tx.get("from"))
        call_tx.setdefault("to", receipt.get("to") if isinstance(receipt, dict) else getattr(receipt, "to", None))
        w3.eth.call(call_tx, block_identifier=block_number)
    except Exception as exc:  # expected: call will raise with revert data
        data_hex = None
        if hasattr(exc, "args") and exc.args:
            arg0 = exc.args[0]
            if isinstance(arg0, dict) and "data" in arg0:
                data_hex = arg0.get("data")
        message = str(exc)
        if not message or message in {"execution reverted", "execution reverted: no data", "('execution reverted', 'no data')"}:
            message = ""  # empty to allow fallback formatting later
        if data_hex is None:
            if "data" in message and "0x" in message:
                data_hex = "0x" + message.split("0x", 1)[1].split(" ")[0]

        decoded = _decode_custom_error(data_hex) if data_hex else None
        if decoded:
            return decoded

        if "revert reason:" in message:
            return message.split("revert reason:", 1)[1].strip()
        if "execution reverted:" in message:
            return message.split("execution reverted:", 1)[1].strip()
        if "execution reverted" in message:
            return message.split("execution reverted", 1)[1].strip().lstrip(": ")
        return message or None

    return None


def _decode_custom_error(data_hex: Optional[str]) -> Optional[str]:
    if not data_hex or not data_hex.startswith("0x"):
        return None
    try:
        data = bytes.fromhex(data_hex[2:])
    except ValueError:
        return None
    if len(data) < 4:
        return None
    selector = data[:4].hex()
    meta = _CUSTOM_ERROR_MAP.get(selector)
    if not meta:
        return None
    name, types = meta
    values = []
    offset = 4
    for typ in types:
        if offset + 32 > len(data):
            return f"{name}(malformed)"
        word = data[offset:offset + 32]
        if typ == "address":
            value = "0x" + word[-20:].hex()
        else:
            value = str(int.from_bytes(word, byteorder="big"))
        values.append(f"{typ}={value}")
        offset += 32
    return f"{name}({', '.join(values)})"


def metamask_tx_request(
    contract: Contract,
    fn_name: str,
    args: list[Any],
    value_wei: int = 0,
    from_address: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal eth_sendTransaction request for MetaMask: {to, data, value}."""
    data_hex: str
    try:
        data_hex = encode_contract_call(contract, fn_name, args)
    except Exception:
        fn = getattr(contract.functions, fn_name)(*(args or []))
        encode_input = getattr(fn, "encode_input", None)
        if callable(encode_input):
            data_hex = encode_input()
        else:
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

