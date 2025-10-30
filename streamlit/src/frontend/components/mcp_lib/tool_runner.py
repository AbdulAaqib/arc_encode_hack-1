from __future__ import annotations

import json
from time import time
from typing import Any, Callable, Dict, Optional

import streamlit as st
from web3 import Web3

from ..wallet import DEFAULT_SESSION_KEY
from ..wallet_connect_component import wallet_command
from .wallet_section import render_wallet_section


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


def render_tool_runner(
    tools_schema: list[Dict[str, Any]],
    function_map: Dict[str, Callable[..., str]],
    w3: Web3,
    key_prefix: str,
    parameter_defaults: Dict[str, Dict[str, Any]] | None = None,
    *,
    role_private_keys: Dict[str, Optional[str]] | None = None,
    role_addresses: Dict[str, str] | None = None,
    tool_role_map: Dict[str, str] | None = None,
) -> None:
    st.subheader("Run a tool")

    if not tools_schema:
        st.info("No MCP tools available. Check contract addresses and ABI paths.")
        return

    tool_names = [entry["function"]["name"] for entry in tools_schema]
    display_names = []
    for name in tool_names:
        role_label = (tool_role_map or {}).get(name)
        if role_label and role_label != "Read-only":
            display_names.append(f"{name} [{role_label}]")
        else:
            display_names.append(name)
    selection_map = dict(zip(display_names, tool_names))
    selected_display = st.selectbox("Choose a tool", display_names, key=f"{key_prefix}_tool_select")
    selected = selection_map[selected_display]

    required_role = (tool_role_map or {}).get(selected)
    if required_role:
        if required_role == "Read-only":
            st.caption("This call is read-only and does not require a signature.")
        else:
            pk_available = bool((role_private_keys or {}).get(required_role))
            addr = (role_addresses or {}).get(required_role)
            if pk_available:
                st.caption(f"Signing will use the {required_role} private key from .env.")
            elif addr:
                st.caption(
                    f"Signing will use MetaMask for the {required_role} wallet ({addr})."
                )
            else:
                st.warning(
                    f"No private key or MetaMask wallet configured for role '{required_role}'."
                )

    mm_state_key = f"mm_state_{key_prefix}_{selected}"
    try:
        expected_chain_id = _normalise_chain_id(w3.eth.chain_id)
    except Exception:
        expected_chain_id = None

    log_key = f"mcp_tool_logs_{key_prefix}_{selected}"
    stored_logs = st.session_state.get(log_key)
    tool_logs: list[str] = stored_logs if isinstance(stored_logs, list) else []

    log_cols = st.columns([4, 1])
    with log_cols[0]:
        log_placeholder = st.empty()
    with log_cols[1]:
        clear_log_clicked = st.button(
            "Clear MetaMask log",
            key=f"{key_prefix}_clear_log_{selected}",
            help="Reset network log for this tool.",
        )

    def _render_logs() -> None:
        if tool_logs:
            log_placeholder.code("\n".join(tool_logs[-40:]), language="text")
        else:
            log_placeholder.info("No MetaMask network events yet.")

    if clear_log_clicked:
        st.session_state.pop(log_key, None)
        tool_logs = []

    _render_logs()

    def _append_log(message: str) -> None:
        text = str(message)
        if not tool_logs or tool_logs[-1] != text:
            tool_logs.append(text)
            st.session_state[log_key] = tool_logs
        _render_logs()

    if expected_chain_id is None:
        _append_log("⚠ Unable to determine ARC chain ID from RPC; MetaMask network enforcement may be limited.")

    existing_state = st.session_state.get(mm_state_key)
    mm_state: Dict[str, Any] = existing_state if isinstance(existing_state, dict) else {}
    mm_state_chain_id: Optional[int] = None
    if mm_state:
        mm_state_chain_id = _normalise_chain_id(mm_state.get("wallet_chain"))
        if mm_state_chain_id is None:
            last_result = mm_state.get("last_result")
            if isinstance(last_result, dict):
                mm_state_chain_id = _normalise_chain_id(last_result.get("chainId"))
        if mm_state_chain_id is None:
            last_value = mm_state.get("last_value")
            if isinstance(last_value, dict):
                mm_state_chain_id = _normalise_chain_id(last_value.get("chainId"))

    session_chain_id: Optional[int] = None
    cached_wallet = st.session_state.get(DEFAULT_SESSION_KEY)
    if isinstance(cached_wallet, dict):
        session_chain_id = _normalise_chain_id(cached_wallet.get("chainId"))
        preferred_address = cached_wallet.get("address")
    else:
        preferred_address = None

    current_chain_id = mm_state_chain_id or session_chain_id
    auto_switch_state_key = f"mm_auto_switch_{key_prefix}_{selected}"
    auto_switch_state = st.session_state.get(auto_switch_state_key)
    if not isinstance(auto_switch_state, dict):
        auto_switch_state = {}

    status_payload = wallet_command(
        key=f"wallet_status_probe_{key_prefix}_{selected}",
        command=None,
        require_chain_id=expected_chain_id,
        preferred_address=str(preferred_address) if preferred_address else None,
        autoconnect=True,
    )

    if isinstance(status_payload, dict):
        mm_state.setdefault("last_value", status_payload)
        payload_chain = _normalise_chain_id(status_payload.get("chainId"))
        payload_status = status_payload.get("status")
        payload_warning = status_payload.get("warning")
        payload_error = status_payload.get("error")
        if payload_chain is not None:
            current_chain_id = payload_chain
            mm_state["wallet_chain"] = payload_chain
            st.session_state[mm_state_key] = mm_state
            st.session_state.setdefault(DEFAULT_SESSION_KEY, {})
            if isinstance(st.session_state[DEFAULT_SESSION_KEY], dict):
                st.session_state[DEFAULT_SESSION_KEY]["chainId"] = payload_chain
        if payload_error:
            _append_log(f"✖ MetaMask error: {payload_error}")
        elif payload_warning:
            _append_log(f"! MetaMask warning: {payload_warning}")
        elif payload_status:
            _append_log(f"(info) MetaMask status: {payload_status}")
        if expected_chain_id is not None and payload_chain is not None:
            if payload_chain == expected_chain_id:
                _append_log(
                    f"✔ Wallet connected to ARC chain {expected_chain_id} (0x{expected_chain_id:x})."
                )
            else:
                _append_log(
                    f"⚠ Wallet is on chain {payload_chain} (0x{payload_chain:x}); expecting ARC {expected_chain_id} (0x{expected_chain_id:x})."
                )
    elif status_payload is not None:
        _append_log(f"(info) MetaMask payload: {status_payload}")

    connected_address = mm_state.get("wallet_address")
    if not connected_address and isinstance(status_payload, dict):
        candidate_address = status_payload.get("address")
        if candidate_address:
            connected_address = str(candidate_address)
            mm_state["wallet_address"] = connected_address
            st.session_state[mm_state_key] = mm_state
            st.session_state.setdefault(DEFAULT_SESSION_KEY, {})
            if isinstance(st.session_state[DEFAULT_SESSION_KEY], dict):
                st.session_state[DEFAULT_SESSION_KEY]["address"] = connected_address
    wallet_connected = bool(connected_address)

    auto_connect_state_key = f"mm_auto_connect_{key_prefix}_{selected}"
    auto_connect_state = st.session_state.get(auto_connect_state_key)
    if not isinstance(auto_connect_state, dict):
        auto_connect_state = {}

    needs_connection = expected_chain_id is not None and not wallet_connected
    if needs_connection:
        pending_connect = bool(auto_connect_state.get("pending"))
        if not auto_connect_state.get("attempted"):
            _append_log("⚠ Auto-requesting MetaMask connection. Choose MetaMask in the provider prompt.")
            sequence = int(time() * 1000)
            auto_connect_state["attempted"] = True
            auto_connect_state["pending"] = True
            auto_connect_state["sequence"] = sequence
            st.session_state[auto_connect_state_key] = auto_connect_state
            pending_connect = True
        sequence = auto_connect_state.get("sequence")
        connect_payload = wallet_command(
            key=f"wallet_auto_connect_{key_prefix}_{selected}",
            command="connect" if pending_connect else None,
            command_sequence=sequence,
            require_chain_id=expected_chain_id,
            preferred_address=str(preferred_address) if preferred_address else None,
            autoconnect=True,
        )
        if isinstance(connect_payload, dict):
            _append_log(f"(info) MetaMask payload: {connect_payload}")
            if pending_connect and connect_payload.get("commandSequence") == sequence:
                auto_connect_state["pending"] = False
                st.session_state[auto_connect_state_key] = auto_connect_state
            payload_error = connect_payload.get("error")
            payload_status = str(connect_payload.get("status") or "").lower()
            payload_address = connect_payload.get("address")
            payload_chain = _normalise_chain_id(connect_payload.get("chainId"))
            if payload_address:
                connected_address = str(payload_address)
                wallet_connected = True
                mm_state["wallet_address"] = connected_address
                st.session_state[mm_state_key] = mm_state
                st.session_state.setdefault(DEFAULT_SESSION_KEY, {})
                if isinstance(st.session_state[DEFAULT_SESSION_KEY], dict):
                    st.session_state[DEFAULT_SESSION_KEY]["address"] = connected_address
            if payload_chain is not None:
                current_chain_id = payload_chain
                mm_state["wallet_chain"] = payload_chain
                st.session_state[mm_state_key] = mm_state
                st.session_state.setdefault(DEFAULT_SESSION_KEY, {})
                if isinstance(st.session_state[DEFAULT_SESSION_KEY], dict):
                    st.session_state[DEFAULT_SESSION_KEY]["chainId"] = payload_chain
            if payload_error:
                _append_log(f"✖ MetaMask connection error: {payload_error}")
            elif payload_status == "connected":
                _append_log("✔ MetaMask connected. Checking network…")
                st.session_state.pop(auto_connect_state_key, None)
            elif payload_status:
                _append_log(f"(info) MetaMask status: {payload_status}")
        elif connect_payload is not None:
            _append_log(f"(info) MetaMask payload: {connect_payload}")
    else:
        if auto_connect_state_key in st.session_state:
            st.session_state.pop(auto_connect_state_key, None)

    if current_chain_id is None:
        current_chain_id = _normalise_chain_id(mm_state.get("wallet_chain"))
    if current_chain_id is None and isinstance(status_payload, dict):
        current_chain_id = _normalise_chain_id(status_payload.get("chainId"))
    chain_mismatch = (
        expected_chain_id is not None
        and current_chain_id is not None
        and current_chain_id != expected_chain_id
    )

    if wallet_connected and chain_mismatch:
        required_hex = f"0x{expected_chain_id:x}" if expected_chain_id is not None else "unknown"
        actual_hex = f"0x{current_chain_id:x}" if current_chain_id is not None else "unknown"
        st.error(
            f"Wallet is connected to chain {current_chain_id} ({actual_hex}); switch to ARC chain {expected_chain_id} ({required_hex}) before running MCP tools."
        )
        st.caption(
            "MetaMask should prompt for a network change. Approve the switch to continue."
        )
        if not auto_switch_state.get("attempted"):
            if expected_chain_id is not None:
                _append_log(
                    f"⚠ Auto-requesting MetaMask network switch to ARC chain {expected_chain_id} (0x{expected_chain_id:x})."
                )
            else:
                _append_log("⚠ Auto-requesting MetaMask network switch to configured chain.")
            sequence = int(time() * 1000)
            auto_switch_state["attempted"] = True
            auto_switch_state["pending"] = True
            auto_switch_state["sequence"] = sequence
            st.session_state[auto_switch_state_key] = auto_switch_state
        pending_switch = bool(auto_switch_state.get("pending"))
        sequence = auto_switch_state.get("sequence")
        switch_payload = wallet_command(
            key=f"wallet_auto_switch_{key_prefix}_{selected}",
            command="switch_network" if pending_switch else None,
            command_sequence=sequence,
            require_chain_id=expected_chain_id,
            command_payload={"require_chain_id": expected_chain_id} if expected_chain_id is not None else None,
            preferred_address=str(preferred_address) if preferred_address else None,
            autoconnect=True,
        )
        if isinstance(switch_payload, dict):
            _append_log(f"(info) MetaMask payload: {switch_payload}")
            if pending_switch and switch_payload.get("commandSequence") == sequence:
                auto_switch_state["pending"] = False
                st.session_state[auto_switch_state_key] = auto_switch_state
            result_chain = _normalise_chain_id(switch_payload.get("chainId"))
            if result_chain is not None:
                current_chain_id = result_chain
                mm_state["wallet_chain"] = result_chain
                st.session_state[mm_state_key] = mm_state
                st.session_state.setdefault(DEFAULT_SESSION_KEY, {})
                if isinstance(st.session_state[DEFAULT_SESSION_KEY], dict):
                    st.session_state[DEFAULT_SESSION_KEY]["chainId"] = result_chain
            status_msg = switch_payload.get("status")
            error_msg = switch_payload.get("error")
            if result_chain == expected_chain_id:
                _append_log(
                    f"✔ Wallet switched to ARC chain {expected_chain_id} (0x{expected_chain_id:x})."
                )
                chain_mismatch = False
                st.session_state.pop(auto_switch_state_key, None)
            elif result_chain is not None and expected_chain_id is not None:
                _append_log(
                    f"! MetaMask reported switch to chain {result_chain} (0x{result_chain:x}); still expecting ARC {expected_chain_id} (0x{expected_chain_id:x})."
                )
            if status_msg:
                st.info(f"MetaMask status: {status_msg}")
                _append_log(f"(info) MetaMask status: {status_msg}")
            if error_msg:
                st.warning(f"MetaMask reported: {error_msg}")
                _append_log(f"✖ MetaMask network switch error: {error_msg}")
        elif switch_payload is not None:
            _append_log(f"! Unexpected MetaMask response: {switch_payload}")
    else:
        if auto_switch_state_key in st.session_state:
            st.session_state.pop(auto_switch_state_key, None)

    if expected_chain_id is not None and wallet_connected and current_chain_id == expected_chain_id:
        _append_log(
            f"✔ Wallet ready on ARC chain {expected_chain_id} (0x{expected_chain_id:x})."
        )

    if expected_chain_id is not None and not wallet_connected:
        st.warning(
            "Connect your MetaMask wallet to continue. If a provider selection window appears, choose MetaMask."
        )

    if isinstance(existing_state, dict) and existing_state.get("metamask"):
        st.markdown("### MetaMask bridge")
        render_wallet_section(existing_state, w3, key_prefix, selected)
        st.info("Complete the wallet action above or clear the MetaMask state before running the tool again.")
        if st.button("Clear MetaMask state", key=f"{mm_state_key}_clear"):
            st.session_state.pop(mm_state_key, None)
            st.experimental_rerun()
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

        widget_key = f"{key_prefix}_param_{selected}_{name}"
        if field_type == "integer":
            value = st.number_input(label, value=int(default or 0), step=1, key=widget_key)
            inputs[name] = int(value)
        elif field_type == "number":
            value = st.number_input(label, value=float(default or 0), key=widget_key)
            inputs[name] = float(value)
        elif field_type == "boolean":
            inputs[name] = st.checkbox(label, value=bool(default) if default is not None else False, key=widget_key)
        elif field_type == "array":
            raw = st.text_area(
                f"{label} (comma separated)",
                value=", ".join(default or []) if isinstance(default, list) else "",
                key=widget_key,
            )
            inputs[name] = [item.strip() for item in raw.split(",") if item.strip()]
        else:
            inputs[name] = st.text_input(
                label,
                value=str(default) if default is not None else "",
                key=widget_key,
            )

    disable_run = chain_mismatch or (expected_chain_id is not None and not wallet_connected)
    if st.button("Run MCP tool", key=f"{key_prefix}_run_tool", disabled=disable_run):
        if expected_chain_id is not None and not wallet_connected:
            st.error("Connect your MetaMask wallet on the ARC network before running this tool.")
            return
        if chain_mismatch:
            st.error("Switch your wallet back to the ARC network before running this tool.")
            return
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

        if isinstance(parsed, dict) and parsed.get("success") and isinstance(parsed.get("metamask"), dict):
            mm = parsed["metamask"]
            state_key = f"mm_state_{key_prefix}_{selected}"
            mm_state = st.session_state.get(state_key, {}) if isinstance(st.session_state.get(state_key), dict) else {}
            mm_state["metamask"] = mm
            st.session_state[state_key] = mm_state
            st.markdown("### MetaMask bridge")
            render_wallet_section(mm_state, w3, key_prefix, selected)
            st.stop()

        try:
            if isinstance(parsed, (list, dict)):
                st.json(parsed)
            else:
                st.write(parsed)
        except Exception:
            st.write(result if isinstance(result, str) else json.dumps(result))
