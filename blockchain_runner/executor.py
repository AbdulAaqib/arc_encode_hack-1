from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path
from typing import Dict, Iterable, Tuple

from .constants import BASE_DIR, LOG_FILE, ENV_VAR_PATTERN, DEFAULT_ENV_FILE
from .env_utils import (
    is_placeholder,
    parse_env_file,
    resolve_env_value,
    set_environment_variable,
)
from .limits import check_amount_limits
from .logging_utils import log_section


def extract_env_vars(command: str) -> set[str]:
    return set(ENV_VAR_PATTERN.findall(command))


def execute_commands(entries: Iterable[Tuple[str, str]]) -> None:
    env: Dict[str, str] = dict(os.environ)
    parse_env_file(DEFAULT_ENV_FILE, env)
    os.environ.update(env)

    current_dir = BASE_DIR

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(f"Logging detailed output to {LOG_FILE}")
    with LOG_FILE.open("w", encoding="utf-8") as log:
        log.write(f"Run started at {timestamp}\n")
        log.write(f"Base directory: {BASE_DIR}\n")
        log.write(f"Command file: {BASE_DIR / 'blockchain_terminal_commands.txt'}\n")
        log.write("=" * 80 + "\n")

        for entry_type, content in entries:
            if entry_type == "comment":
                log_section(log, f"# {content}")
                continue

            command = content.strip()
            if not command:
                continue

            print(f"→ {command}")
            log.write("\n" + "-" * 80 + "\n")
            log.write(f"Timestamp: {datetime.datetime.now(datetime.timezone.utc).isoformat()}\n")
            log.write(f"Working directory: {current_dir}\n")
            log.write(f"Command: {command}\n")

            # Handle built-in directives before invoking the shell
            if command.startswith("cd "):
                target = command[3:].strip()
                new_dir = Path(target)
                if not new_dir.is_absolute():
                    new_dir = (current_dir / new_dir).resolve()
                current_dir = new_dir
                log_section(log, "Result: changed directory", str(current_dir))
                continue

            if command.startswith("source "):
                path_str = command[len("source ") :].strip()
                env_path = Path(path_str)
                if not env_path.is_absolute():
                    env_path = (current_dir / env_path).resolve()
                parse_env_file(env_path, env)
                os.environ.update(env)
                log_section(log, "Result: sourced env file", str(env_path))
                continue

            if command.startswith("export "):
                assignment = command[len("export ") :].strip()
                key, value, placeholder = set_environment_variable(env, assignment)
                if placeholder:
                    current_value = env.get(key)
                    if current_value:
                        log_section(
                            log,
                            "Skipped placeholder export",
                            f"{key} retains existing value: {current_value}",
                        )
                    else:
                        log_section(
                            log,
                            "Skipped placeholder export",
                            f"{key} remains unset (placeholder provided: {value})",
                        )
                else:
                    log_section(log, "Result: set environment variable", f"{key}={value}")
                continue

            env_vars = extract_env_vars(command)
            missing_vars = []
            placeholder_vars = []
            for var in env_vars:
                value = resolve_env_value(var, env)
                if not value:
                    missing_vars.append(var)
                    continue
                if is_placeholder(value):
                    placeholder_vars.append((var, value))

            if missing_vars:
                log_section(
                    log,
                    "Skipped command (missing env)",
                    ", ".join(f"${name}" for name in missing_vars),
                )
                continue

            if placeholder_vars:
                log_section(
                    log,
                    "Skipped command (placeholder env)",
                    ", ".join(f"${var}={value}" for var, value in placeholder_vars),
                )
                continue

            amount_error = check_amount_limits(command)
            if amount_error:
                log_section(log, "Skipped command (amount limit)", amount_error)
                continue

            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=str(current_dir),
                    check=False,
                )
            except Exception as exc:  # pylint: disable=broad-except
                log_section(log, "Execution failed", repr(exc))
                continue

            log.write(f"Exit code: {result.returncode}\n")
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            log.write("STDOUT:\n")
            log.write(stdout if stdout else "<empty>\n")
            log.write("STDERR:\n")
            log.write(stderr if stderr else "<empty>\n")
            log.flush()

            status = "completed" if result.returncode == 0 else f"completed with exit code {result.returncode}"
            print(f"← {status}. See {LOG_FILE} for details.")

