from __future__ import annotations

from pathlib import Path
import re


BASE_DIR = Path(__file__).resolve().parent.parent
COMMAND_FILE = BASE_DIR / "blockchain_terminal_commands.txt"
LOG_FILE = BASE_DIR / "blockchain_command_results.log"
DEFAULT_ENV_FILE = BASE_DIR / ".env"

PLACEHOLDER_MARKERS = ("YOUR", "REPLACE", "<", ">")
ENV_VAR_PATTERN = re.compile(r"(?<!\\)\$([A-Za-z_][A-Za-z0-9_]*)")


__all__ = [
    "BASE_DIR",
    "COMMAND_FILE",
    "LOG_FILE",
    "DEFAULT_ENV_FILE",
    "PLACEHOLDER_MARKERS",
    "ENV_VAR_PATTERN",
]

