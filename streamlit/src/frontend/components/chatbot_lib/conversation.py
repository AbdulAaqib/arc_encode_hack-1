from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, Iterable, Optional

import streamlit as st

from ..toolkit import render_tool_message, tool_error, tool_success


logger = logging.getLogger("arc.mcp.tools")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[MCP] %(levelname)s %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def _truncate_output(value: str, limit: int = 800) -> str:
    if not value:
        return value or ""
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


def stream_chunks(stream: Iterable) -> Iterable[str]:
    """Yield token deltas from the streaming Azure OpenAI response."""

    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


def run_mcp_llm_conversation(
    client: Any,
    deployment: str,
    messages: list[Dict[str, Any]],
    tools_schema: list[Dict[str, Any]],
    function_map: Dict[str, Any],
    *,
    wallet_widget_callback: Any = None,
    status_callback: Optional[Callable[[Optional[str]], None]] = None,
) -> None:
    pending = client.chat.completions.create(
        model=deployment,
        messages=messages,
        tools=tools_schema,
        tool_choice="auto",
    )

    logger.info("Starting MCP conversation loop...")

    tool_call_count = 0
    max_tool_calls = 50  # Prevent infinite loops

    wallet_pause_requested = False

    while True:
        message = pending.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []

        if tool_calls:
            tool_call_count += len(tool_calls)
            if tool_call_count > max_tool_calls:
                logger.warning(
                    "Reached max tool calls (%d), exiting conversation loop",
                    max_tool_calls,
                )
                with st.chat_message("assistant"):
                    st.warning(
                        f"Reached maximum tool call limit ({max_tool_calls}). "
                        "If a transaction is pending, please approve it in MetaMask and I'll continue."
                    )
                break
            messages.append(message.model_dump())
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                args_payload = tool_call.function.arguments or "{}"
                try:
                    arguments = json.loads(args_payload) if args_payload else {}
                except json.JSONDecodeError:
                    arguments = {}

                logger.info(
                    "Tool call '%s' invoked with args: %s", tool_name, arguments
                )

                handler = function_map.get(tool_name)
                if handler is None:
                    logger.warning("Tool '%s' is not registered.", tool_name)
                    tool_output = tool_error(f"Tool '{tool_name}' is not registered.")
                else:
                    try:
                        if status_callback:
                            try:
                                status_callback(tool_name)
                            except Exception:
                                logger.exception("Status callback raised an error while starting '%s'", tool_name)
                        logger.info("Tool '%s' executing...", tool_name)
                        response_payload = handler(**arguments)
                        tool_output = (
                            response_payload
                            if isinstance(response_payload, str)
                            else tool_success(response_payload)
                        )

                        # Check if tool returned a MetaMask transaction request
                        try:
                            parsed = (
                                json.loads(tool_output)
                                if isinstance(tool_output, str)
                                else tool_output
                            )
                            if (
                                isinstance(parsed, dict)
                                and parsed.get("success")
                                and "metamask" in parsed
                            ):
                                metamask_data = parsed["metamask"]
                                tx_request = metamask_data.get("tx_request")
                                if tx_request:
                                    # Store pending transaction in session for wallet widget to display
                                    sequence = int(time.time() * 1000)
                                    pending_cmd = {
                                        "command": "send_transaction",
                                        "tx_request": tx_request,
                                        "label": metamask_data.get(
                                            "hint", "Confirm Transaction"
                                        ),
                                        "sequence": sequence,
                                    }
                                    # Include chainId if specified for the transaction
                                    if "chainId" in metamask_data:
                                        pending_cmd["chainId"] = metamask_data["chainId"]
                                        # Also add it to tx_request for compatibility
                                        if isinstance(tx_request, dict):
                                            tx_request["chainId"] = metamask_data["chainId"]
                                    st.session_state[
                                        "chatbot_wallet_pending_command"
                                    ] = pending_cmd
                                    st.session_state["chatbot_needs_tx_rerun"] = True
                                    st.session_state["chatbot_waiting_for_wallet"] = (
                                        True
                                    )
                                    wallet_pause_requested = True
                                    logger.info(
                                        "Stored transaction request for GPT-triggered MetaMask popup"
                                    )
                                    tool_output = json.dumps(parsed)
                        except Exception:
                            pass  # Keep original tool_output if parsing fails

                        logger.info("Tool '%s' completed successfully", tool_name)
                    except Exception as exc:  # pragma: no cover - surfaced via UI only
                        logger.exception(
                            "Tool '%s' raised an exception: %s", tool_name, exc
                        )
                        tool_output = tool_error(str(exc))
                    finally:
                        if status_callback:
                            try:
                                status_callback(None)
                            except Exception:
                                logger.exception("Status callback raised an error while finishing '%s'", tool_name)

                logger.info(
                    "Tool '%s' response: %s",
                    tool_name,
                    _truncate_output(tool_output),
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": tool_output,
                    }
                )
                render_tool_message(tool_name, tool_output)

            if wallet_pause_requested:
                logger.info(
                    "Wallet approval required – pausing MCP conversation loop until MetaMask responds."
                )
                break

            pending = client.chat.completions.create(
                model=deployment,
                messages=messages,
                tools=tools_schema,
                tool_choice="auto",
            )
            continue

        content = getattr(message, "content", None)
        if content:
            messages.append({"role": "assistant", "content": content})
            with st.chat_message("assistant"):
                st.markdown(content)
        logger.info("MCP conversation loop complete. Exiting.")
        break

    if status_callback:
        try:
            status_callback(None)
        except Exception:
            logger.exception("Status callback raised an error during final reset")

    if wallet_pause_requested:
        return
