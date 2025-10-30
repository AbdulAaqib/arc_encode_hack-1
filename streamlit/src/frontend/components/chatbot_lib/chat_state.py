from __future__ import annotations

import streamlit as st

from .constants import MCP_SYSTEM_PROMPT


def initialize_chat_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "Hi! I'm Doggo, your PawChain guide. Ask me anything about Arc credit flows or this dashboard.",
            }
        ]

    msgs = st.session_state.messages
    if not msgs or msgs[0].get("role") != "system":
        msgs.insert(0, {"role": "system", "content": MCP_SYSTEM_PROMPT})


def append_message(role: str, content: str) -> None:
    st.session_state.messages.append({"role": role, "content": content})
