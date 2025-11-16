"""Sidebar navigation component for the Streamlit dashboard."""

from __future__ import annotations

import streamlit as st


PAGE_ENTRIES: tuple[tuple[str, str], ...] = (
    ("SnifferBank Home", "🏠 SnifferBank Home"),
    ("Chatbot", "💬 Chatbot"),
    ("MCP Tools", "🧰 MCP Tools"),
)


def render_navigation() -> str:
    """Render the sidebar navigation and return the selected page."""

    with st.sidebar:
        if "active_page" not in st.session_state:
            st.session_state["active_page"] = PAGE_ENTRIES[0][0]

        st.markdown("<div style='min-height:3rem'></div>", unsafe_allow_html=True)

        active = st.session_state["active_page"]

        for internal_name, label in PAGE_ENTRIES:
            button_type = "primary" if active == internal_name else "secondary"
            if st.button(label, use_container_width=True, type=button_type):
                st.session_state["active_page"] = internal_name
                active = internal_name

    return active
