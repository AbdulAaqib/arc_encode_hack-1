from __future__ import annotations


def log_section(log_handle, header: str, content: str | None = None) -> None:
    log_handle.write(f"{header}\n")
    if content:
        log_handle.write(f"{content}\n")
    log_handle.flush()

    # Mirror the most important log events to stdout so the user sees progress
    print(header)
    if content:
        print(content)

