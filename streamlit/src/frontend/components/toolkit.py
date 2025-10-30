"""Toolkit facade module.

This aggregator re-exports the public functions from the refactored
`toolkit_lib` package so existing imports keep working while code is
organized into smaller modules.
"""

from __future__ import annotations

from .toolkit_lib.messages import tool_success, tool_error, render_tool_message
from .toolkit_lib.history import render_llm_history
from .toolkit_lib.sbt_tools import build_llm_toolkit
from .toolkit_lib.pool_tools import build_lending_pool_toolkit
from .toolkit_lib.tx_helpers import (
    fee_params,
    next_nonce,
    sign_and_send,
    format_receipt,
    metamask_tx_request,
)

__all__ = [
    "tool_success",
    "tool_error",
    "render_tool_message",
    "render_llm_history",
    "build_llm_toolkit",
    "build_lending_pool_toolkit",
    "fee_params",
    "next_nonce",
    "sign_and_send",
    "format_receipt",
    "metamask_tx_request",
]
