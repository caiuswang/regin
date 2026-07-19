"""Loopback ping so out-of-process producers can wake the badge push.

A hook or MCP process writes an inbox row or a drift finding in its own
interpreter and has no reach into the web process's socket set, so it POSTs a
bare trigger to the local dashboard, which then recomputes and fans out.

Best-effort throughout: a notify must never break — or noticeably delay — the
producer. `record_message` runs synchronously inside a PostToolUse hook, on
the user's tool-call latency, and `record_findings` documents itself as a hot
path, so the dashboard being *down* (the common case for a hook) must cost
approximately nothing. `urlopen` does not give that: on a refused port it
burns the entire timeout before raising, so the connection is probed on a raw
socket first and the request is only attempted once something is listening.
A daemon thread would be the other option, but hook processes exit
immediately after the write and would kill it before it delivered.
"""

from __future__ import annotations

import socket
import urllib.request

_PROBE_TIMEOUT_SECONDS = 0.05
_REQUEST_TIMEOUT_SECONDS = 0.25
_PATH = "/api/internal/notify"


def notify_counts_changed() -> None:
    try:
        _post_notify(_web_port())
    except Exception:  # noqa: BLE001 — see module docstring
        return


def _web_port() -> int:
    from lib.settings import settings
    return settings.web_port


def _post_notify(port: int) -> None:
    if not _is_listening(port):
        return
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{_PATH}",
        data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS):
        pass


def _is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port),
                                      timeout=_PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


__all__ = ["notify_counts_changed"]
