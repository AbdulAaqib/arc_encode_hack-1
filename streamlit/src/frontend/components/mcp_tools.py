"""MCP Tools aggregator with lazy loading.

Re-exports the functions split into the ``mcp_lib`` package while keeping
backwards-compatible names for existing imports.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "_st_rerun",
    "_render_wallet_section",
    "_render_tool_runner",
    "render_mcp_tools_page",
]


def _load_module() -> Any:
    return import_module("..mcp_lib", __name__)


def _st_rerun(*args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
    return _load_module()._st_rerun(*args, **kwargs)


def _render_wallet_section(*args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
    return _load_module()._render_wallet_section(*args, **kwargs)


def _render_tool_runner(*args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
    return _load_module()._render_tool_runner(*args, **kwargs)


def render_mcp_tools_page(*args: Any, **kwargs: Any) -> Any:
    return _load_module().render_mcp_tools_page(*args, **kwargs)

