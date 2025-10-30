from __future__ import annotations

import json
from time import time
from typing import Any, Dict, Optional

import streamlit as st
from web3 import Web3

from ..wallet import DEFAULT_SESSION_KEY
from ..wallet_connect_component import wallet_command
from .rerun import st_rerun


def _normalise_chain_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped, 0)
        except ValueError:
            try:
                return int(stripped)
            except ValueError:
                return None
    return None


def render_wallet_section(mm_state: Dict[str, Any], w3: Web3, key_prefix: str, selected: str) -> None:
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
        if isinstance(component_value, dict):
            component_chain = _normalise_chain_id(component_value.get("chainId"))
            if component_chain is not None:
                mm_state["wallet_chain"] = component_chain
        if (
            isinstance(pending, dict)
            and isinstance(component_value, dict)
            and component_value.get("commandSequence") == pending.get("sequence")
        ):
            mm_state["last_result"] = component_value
            mm_state["pending_command"] = None
            addr = component_value.get("address")
            if addr:
                mm_state["wallet_address"] = addr
            chain = component_value.get("chainId")
            if chain:
                mm_state["wallet_chain"] = chain

    required_chain_id = _normalise_chain_id(chain_id)
    wallet_chain_id = _normalise_chain_id(mm_state.get("wallet_chain"))
    if wallet_chain_id is None:
        cached_wallet = st.session_state.get(DEFAULT_SESSION_KEY, {})
        if isinstance(cached_wallet, dict):
            wallet_chain_id = _normalise_chain_id(cached_wallet.get("chainId"))
            if wallet_chain_id is not None:
                mm_state["wallet_chain"] = wallet_chain_id

    chain_mismatch = (
        required_chain_id is not None
        and wallet_chain_id is not None
        and wallet_chain_id != required_chain_id
    )
    if chain_mismatch:
        pending = mm_state.get("pending_command")
        auto_switch_attempted = bool(mm_state.get("_auto_switch_attempted"))
        if (
            required_chain_id is not None
            and pending is None
            and not auto_switch_attempted
        ):
            sequence = int(time() * 1000)
            mm_state["pending_command"] = {
                "command": "switch_network",
                "payload": {"require_chain_id": required_chain_id},
                "sequence": sequence,
            }
            mm_state["_auto_switch_attempted"] = True
            st.session_state[f"mm_state_{key_prefix}_{selected}"] = mm_state
            st_rerun()
        required_hex = f"0x{required_chain_id:x}"
        actual_hex = f"0x{wallet_chain_id:x}"
        st.error(
            f"Wallet is connected to chain {wallet_chain_id} ({actual_hex}); switch to the required chain {required_chain_id} ({required_hex}) before sending this transaction."
        )

    status_cols = st.columns(2)
    with status_cols[0]:
        wallet_addr = mm_state.get("wallet_address") or preferred_address
        if wallet_addr:
            st.info(f"Cached wallet: {wallet_addr}")
        else:
            st.info("No wallet connected yet.")
    with status_cols[1]:
        if required_chain_id is not None:
            st.info(f"Required chain: {required_chain_id} (0x{required_chain_id:x})")
        if wallet_chain_id is not None:
            st.caption(f"Wallet chain: {wallet_chain_id} (0x{wallet_chain_id:x})")
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
        st_rerun()

    if btn_cols[1].button("Switch network", key=f"btn_switch_{key_prefix}_{selected}"):
        mm_state["pending_command"] = {
            "command": "switch_network",
            "payload": {"require_chain_id": chain_id},
            "sequence": int(time() * 1000),
        }
        st.session_state[f"mm_state_{key_prefix}_{selected}"] = mm_state
        st_rerun()

    send_disabled = tx_req is None or chain_mismatch
    if btn_cols[2].button("Send transaction", key=f"btn_send_{key_prefix}_{selected}", disabled=send_disabled):
        mm_state["pending_command"] = {
            "command": "send_transaction",
            "payload": {"tx_request": tx_req, "action": action},
            "sequence": int(time() * 1000),
        }
        st.session_state[f"mm_state_{key_prefix}_{selected}"] = mm_state
        st_rerun()

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
        st_rerun()

    if not chain_mismatch and mm_state.get("_auto_switch_attempted"):
        mm_state["_auto_switch_attempted"] = False
