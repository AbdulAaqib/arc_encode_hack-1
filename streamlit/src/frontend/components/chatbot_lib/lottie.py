from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_lottie_json(filepath: Path) -> dict[str, Any] | None:
    try:
        with filepath.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
