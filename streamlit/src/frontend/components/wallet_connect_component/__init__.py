import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st
import streamlit.components.v1 as components

# Streamlit custom component: Wallet Connect (React)
# Usage in pages:
#   from .wallet_connect_component import connect_wallet
#   info = connect_wallet(key="wallet_connect")
#   if isinstance(info, dict) and info.get("address"):
#       st.session_state["wallet_address"] = info["address"]


def _declare_component() -> Any:
    """Declare the Streamlit component.

    - Prefer static assets under `frontend/build` so no dev server is required.
    - Allow overriding with `WALLET_CONNECT_DEV_URL` during local development.
    """
    dev_url = os.getenv("WALLET_CONNECT_DEV_URL")
    if dev_url:
        return components.declare_component("wallet_connect", url=dev_url)

    build_dir = Path(__file__).parent / "frontend" / "build"
    index_html = build_dir / "index.html"
    if not index_html.exists():
        raise RuntimeError(
            "Wallet Connect component build not found.\n"
            "Run the frontend build once before using the component:\n"
            f"  cd {build_dir.parent}\n"
            "  npm install\n"
            "  npm run build"
        )

    return components.declare_component("wallet_connect", path=str(build_dir))


_component = _declare_component()


def connect_wallet(
    key: Optional[str] = None,
    require_chain_id: Optional[int] = None,
    tx_request: Optional[dict] = None,
    action: Optional[str] = None,
    tx_label: Optional[str] = None,
    preferred_address: Optional[str] = None,
    autoconnect: Optional[bool] = None,
    mode: Optional[str] = None,
    command: Optional[str] = None,
    command_payload: Optional[Dict[str, Any]] = None,
    command_sequence: Optional[int] = None,
) -> Any:
    """Render the wallet connect UI and return the payload from the frontend.

    Args:
        key: Streamlit component key.
        require_chain_id: Optional chain id to enforce/match against the injected wallet.
        tx_request: Optional transaction request dict to be sent via the injected wallet (MetaMask/Rabby).
        action: Optional action hint (e.g., "eth_sendTransaction") for the frontend.
        tx_label: Optional button label to display for the transaction action.
        preferred_address: Optional cached/remembered address to hint to the UI.
        autoconnect: If True, attempts a silent authorization via eth_accounts on mount.
        mode: Optional mode for the component (e.g., "interactive" or "headless").
        command: Optional command to execute in headless mode (e.g., "connect", "send_transaction").
        command_payload: Optional payload dict for the command (must be JSON serializable).
        command_sequence: Optional sequence/id to distinguish repeated commands.
    """
    args: dict = {}
    if require_chain_id is not None:
        args["require_chain_id"] = require_chain_id
    if tx_request is not None:
        args["tx_request"] = tx_request
    if action is not None:
        args["action"] = action
    if tx_label is not None:
        args["tx_label"] = tx_label
    if preferred_address:
        args["preferred_address"] = preferred_address
    if autoconnect is not None:
        args["autoconnect"] = bool(autoconnect)
    if mode:
        args["mode"] = mode
    if command:
        args["command"] = command
    if command_payload is not None:
        args["command_payload"] = command_payload
    if command_sequence is not None:
        args["command_sequence"] = command_sequence
    return _component(default=None, key=key, **args)


def wallet_command(
    *,
    key: str,
    command: Optional[str],
    require_chain_id: Optional[int] = None,
    tx_request: Optional[Dict[str, Any]] = None,
    action: Optional[str] = None,
    tx_label: Optional[str] = None,
    preferred_address: Optional[str] = None,
    autoconnect: Optional[bool] = None,
    command_payload: Optional[Dict[str, Any]] = None,
    command_sequence: Optional[int] = None,
) -> Any:
    """Execute a headless wallet command and return the component payload."""

    if command_sequence is not None:
        sequence = command_sequence
    elif command is not None:
        sequence = int(time.time() * 1000)
    else:
        sequence = None
    return connect_wallet(
        key=key,
        require_chain_id=require_chain_id,
        tx_request=tx_request,
        action=action,
        tx_label=tx_label,
        preferred_address=preferred_address,
        autoconnect=autoconnect,
        mode="headless",
        command=command,
        command_payload=command_payload,
        command_sequence=sequence,
    )


if __name__ == "__main__":
    st.title("Wallet Connect Component Preview")
    info = connect_wallet(key="wallet_connect")
    st.write(info)
