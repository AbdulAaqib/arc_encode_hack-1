from __future__ import annotations

import shlex
from typing import Optional

def parse_int(token: str) -> Optional[int]:
    if not token or token.startswith("$"):
        return None
    try:
        if token.startswith("0x") or token.startswith("0X"):
            return int(token, 16)
        sanitized = token.replace("_", "")
        return int(sanitized, 10)
    except ValueError:
        return None


def check_amount_limits(command: str) -> Optional[str]:
    tokens = shlex.split(command)
    monitored_functions = {
        "deposit(uint256)",
        "withdraw(uint256)",
        "repay(uint256)",
        "openLoan(address,uint256,uint256)",
    }

    for idx, token in enumerate(tokens):
        normalized = token.strip('"')
        if normalized not in monitored_functions:
            continue

        # Determine which argument position to inspect (first numeric argument)
        next_idx = idx + 1
        numeric_found = False
        while next_idx < len(tokens):
            candidate = tokens[next_idx]
            next_idx += 1

            # Skip RPC flags or other options
            if candidate.startswith("--"):
                continue

            value = parse_int(candidate)
            if value is None:
                # likely address/env var; continue scanning
                continue

            numeric_found = True
            break

        if not numeric_found and normalized != "openLoan(address,uint256,uint256)":
            # For deposit/withdraw/repay we expect an explicit numeric argument
            return f"no numeric amount found for {normalized}"

    return None

