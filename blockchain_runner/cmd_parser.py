from __future__ import annotations

from pathlib import Path
from typing import List, Tuple


def parse_command_file(path: Path) -> List[Tuple[str, str]]:
    """Return a list of (entry_type, content) pairs from the command file.

    ``entry_type`` is either ``"comment"`` or ``"command"``. Multi-line
    commands that rely on ``\\`` continuation are flattened into a single
    command string.
    """

    entries: List[Tuple[str, str]] = []
    buffer = ""

    for raw_line in path.read_text().splitlines():
        stripped = raw_line.strip()

        # Preserve stand-alone comments
        if not buffer and (not stripped or stripped.startswith("#")):
            if stripped:
                entries.append(("comment", stripped))
            continue

        line = raw_line.rstrip()
        if line.endswith("\\"):
            buffer += line[:-1].rstrip() + " "
            continue

        buffer += line.strip()
        if buffer:
            entries.append(("command", buffer.strip()))
        buffer = ""

    if buffer:
        entries.append(("command", buffer.strip()))

    return entries

