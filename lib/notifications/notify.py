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

import json
import socket
import urllib.request

_PROBE_TIMEOUT_SECONDS = 0.05
_REQUEST_TIMEOUT_SECONDS = 0.25
_PATH = "/api/internal/notify"


def notify_counts_changed() -> None:
    _trigger({})


def notify_message(message_id: int | None) -> None:
    """Push one newly written inbox row to the notification surfaces.

    Carries only the id — the dashboard re-reads the row, so this stays a
    trigger rather than a second, forgeable copy of the record.
    """
    if message_id is None:
        notify_counts_changed()
        return
    _trigger({"message_id": int(message_id)})


def notify_resolved(*, trace_id: str, msg_key: str | None = None,
                    message_ids: list[int] | None = None,
                    reason: str = "dismissed") -> None:
    """Retire a live notification whose condition was handled, so an open
    blocker banner clears without waiting for the next page load.

    `reason="dismissed"` means the underlying condition is gone (the prompt was
    answered); only that retires a blocker. See `_retire` in
    `web/blueprints/trace/agent_messages.py` for why reading is not answering.
    """
    payload = {"trace_id": trace_id, "reason": reason}
    if msg_key:
        payload["msg_key"] = msg_key
    if message_ids:
        payload["message_ids"] = [int(i) for i in message_ids]
    _trigger({"resolved": payload})


def _trigger(body: dict) -> None:
    try:
        _post_notify(_web_port(), body)
    except Exception:  # noqa: BLE001 — see module docstring
        return


def _web_port() -> int:
    from lib.settings import settings
    return settings.web_port


def _post_notify(port: int, body: dict) -> None:
    if not _is_listening(port):
        return
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{_PATH}",
        data=json.dumps(body).encode("utf-8"), method="POST",
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


__all__ = ["notify_counts_changed", "notify_message", "notify_resolved"]
