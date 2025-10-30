"""Web3 helper utilities for the Streamlit frontend."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Sequence

from web3 import Web3
from web3.contract import Contract


def get_web3_client(rpc_url: Optional[str]) -> Optional[Web3]:
    """Create a Web3 client if an RPC URL is provided and reachable.

    Returns None if rpc_url is falsy or if initialization fails.
    """
    if not rpc_url:
        return None
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        # Optional ping; if provider is down this may raise
        _ = w3.eth.chain_id  # noqa: F841
        return w3
    except Exception:
        return None


def load_contract_abi(abi_path: Optional[str]) -> Optional[list[dict[str, Any]]]:
    """Load a contract ABI JSON from disk.

    Accepts an absolute or relative path string. Returns None on any error.
    """
    if not abi_path:
        return None
    try:
        p = Path(abi_path).expanduser().resolve()
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)
        # Some artifact JSONs wrap the ABI under an "abi" key
        if isinstance(data, dict) and "abi" in data and isinstance(data["abi"], list):
            return data["abi"]  # type: ignore[return-value]
        if isinstance(data, list):
            return data  # type: ignore[return-value]
        return None
    except Exception:
        return None


def encode_contract_call(contract: Contract, fn_name: str, args: Sequence[Any] | None = None) -> str:
    """Encode a contract function call, compatible with Web3.py v5/v6."""
    call_args = list(args or [])

    def _try_encode(method_name: str) -> Optional[str]:
        encode_fn = getattr(contract, method_name, None)
        if not callable(encode_fn):
            return None
        for key in ("fn_name", "function_name"):
            try:
                return encode_fn(**{key: fn_name, "args": call_args})
            except TypeError:
                continue
        try:
            return encode_fn(fn_name, args=call_args)
        except TypeError:
            pass
        try:
            return encode_fn(fn_name, call_args)
        except TypeError:
            pass
        return None

    for candidate in ("encode_abi", "encodeABI"):
        encoded = _try_encode(candidate)
        if encoded is not None:
            return encoded

    fn = getattr(contract.functions, fn_name)(*call_args)
    encode_input = getattr(fn, "encode_input", None)
    if callable(encode_input):
        return encode_input()
    encode_tx = getattr(fn, "_encode_transaction_data", None)
    if callable(encode_tx):
        return encode_tx()
    raise AttributeError(f"Unable to encode contract call for '{fn_name}'.")
