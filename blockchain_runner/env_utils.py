from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from .constants import PLACEHOLDER_MARKERS

# Map canonical env names to alternative aliases that may appear in the command file
ENV_ALIASES: Dict[str, list[str]] = {
    "TRUST_MINT_SBT_ADDRESS": ["SBT_ADDRESS"],
    "BORROWER_ADDRESS": ["BORROWER_ADDRESS"],
    "BORROWER_PRIVATE_KEY": ["PRIVATE_KEY"],
    "LENDER_ADDRESS": ["INITIAL_OWNER_ADDRESS"],
    "LENDER_PRIVATE_KEY": ["PRIVATE_KEY"],
    "USDC_ADDRESS": ["ArcTestnetUSDC"],
}


def parse_env_file(path: Path, env: Dict[str, str]) -> None:
    """Load KEY=VALUE pairs from a .env-style file into ``env``.

    Lines starting with ``#`` or blank lines are ignored. Values wrapped in
    single or double quotes have their surrounding quotes stripped.
    """

    if not path.exists():
        return

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith("\"") and value.endswith("\"")) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        env[key] = value


def is_placeholder(value: str) -> bool:
    upper_value = value.upper()
    return any(marker in upper_value for marker in PLACEHOLDER_MARKERS)


def resolve_env_value(name: str, env: Dict[str, str]) -> str | None:
    value = env.get(name)
    if value:
        return value
    for alias in ENV_ALIASES.get(name, []):
        alias_value = env.get(alias)
        if alias_value:
            env[name] = alias_value
            return alias_value
    return None


def set_environment_variable(env: Dict[str, str], assignment: str) -> tuple[str, str, bool]:
    key, _, value = assignment.partition("=")
    key = key.strip()
    value = value.strip()
    if (value.startswith("\"") and value.endswith("\"")) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    placeholder = is_placeholder(value)
    if not placeholder:
        env[key] = value
        os.environ[key] = value
    return key, value, placeholder

