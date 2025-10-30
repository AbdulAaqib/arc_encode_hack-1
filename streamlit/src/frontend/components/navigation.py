"""Sidebar navigation component for the Streamlit dashboard."""

from __future__ import annotations

import streamlit as st


PAGES: tuple[str, ...] = ("Intro", "Chatbot", "Wallet", "MCP Tools")


def render_navigation() -> str:
    """Render the sidebar navigation and return the selected page."""

    with st.sidebar:
        st.title("Navigation")
        selected = st.radio("Choose a view", PAGES, index=0)
    return selected

