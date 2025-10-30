#!/usr/bin/env python3
"""Thin wrapper to execute blockchain terminal commands and log outputs.

This script now delegates parsing and execution to the ``blockchain_runner``
package, which organizes the logic into smaller, maintainable modules.
"""

from __future__ import annotations

from blockchain_runner import COMMAND_FILE, parse_command_file, execute_commands


def main() -> None:
    if not COMMAND_FILE.exists():
        raise FileNotFoundError(f"Command file not found: {COMMAND_FILE}")

    entries = parse_command_file(COMMAND_FILE)
    execute_commands(entries)


if __name__ == "__main__":
    main()

