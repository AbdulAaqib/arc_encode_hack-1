from __future__ import annotations

from typing import Any, Dict, Iterable

import streamlit as st

from .messages import render_tool_message, _render_user_message


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

