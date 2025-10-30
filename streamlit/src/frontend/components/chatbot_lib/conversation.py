from __future__ import annotations

import json
from typing import Any, Dict, Iterable

import streamlit as st

from ..toolkit import render_tool_message, tool_error, tool_success


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
) -> None:
    pending = client.chat.completions.create(
        model=deployment,
        messages=messages,
        tools=tools_schema,
        tool_choice="auto",
    )

    while True:
        message = pending.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []

        if tool_calls:
            messages.append(message.model_dump())
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                args_payload = tool_call.function.arguments or "{}"
                try:
                    arguments = json.loads(args_payload) if args_payload else {}
                except json.JSONDecodeError:
                    arguments = {}

                handler = function_map.get(tool_name)
                if handler is None:
                    tool_output = tool_error(f"Tool '{tool_name}' is not registered.")
                else:
                    try:
                        response_payload = handler(**arguments)
                        tool_output = (
                            response_payload
                            if isinstance(response_payload, str)
                            else tool_success(response_payload)
                        )
                    except Exception as exc:  # pragma: no cover - surfaced via UI only
                        tool_output = tool_error(str(exc))

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": tool_output,
                    }
                )
                render_tool_message(tool_name, tool_output)

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
        break
