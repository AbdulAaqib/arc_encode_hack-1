"""Chatbot aggregator module.

Provides backwards-compatible names while delegating implementation to the
new ``chatbot_lib`` package.
"""

from __future__ import annotations

from .chatbot_lib import (
    AZURE_DEPLOYMENT_ENV,
    MCP_SYSTEM_PROMPT,
    WAVES_PATH,
    _create_azure_client,
    _initialize_chat_state,
    _append_message,
    _extract_text_from_upload,
    _build_attachment_context,
    _stream_chunks,
    _run_mcp_llm_conversation,
    _load_lottie_json,
    render_chatbot_page,
    render_mcp_llm_playground_section,
)

__all__ = [
    "AZURE_DEPLOYMENT_ENV",
    "MCP_SYSTEM_PROMPT",
    "WAVES_PATH",
    "_create_azure_client",
    "_initialize_chat_state",
    "_append_message",
    "_extract_text_from_upload",
    "_build_attachment_context",
    "_stream_chunks",
    "_run_mcp_llm_conversation",
    "_load_lottie_json",
    "render_chatbot_page",
    "render_mcp_llm_playground_section",
]

