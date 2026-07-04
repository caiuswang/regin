"""Guarded delivery of a message into a live claude session's tmux pane.

Promotes the proven spike (`scripts/bridge_mvp.py`) to library code, with
two additions the spike could not do: the target pane is resolved from the
slice-1 registry (`bridge_panes`) instead of a caller-supplied target, and
delivery re-checks the pane's identity against the registered triple to
refuse ids recycled by a tmux server restart. See
`docs/agent-bridge-design.md` (*Delivery guards*, *Pane identity and
staleness*).

`deliver()` NEVER raises for an expected failure — a missing pane, a stale
id, a non-claude foreground command, an unverifiable ack, a tripped rate
limit, or the bridge being disabled all resolve to
`DeliveryResult(delivered=False, detail=...)`. Every outcome is audited via
`activity_log('agent_bridge')`.

tmux calls thread the registered `tmux_socket` (the first comma-field of
`$TMUX` captured at registration) so a session on a non-default socket is
reached with `-S <socket>`; a NULL socket omits the flag (default socket).
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from typing import NamedTuple

from lib.activity_log import get_activity_logger
from lib.agent_bridge import store
from lib.settings import settings

log = get_activity_logger("agent_bridge")

# tmux subprocess guard — a hung socket must not stall the delivery path.
_TMUX_TIMEOUT_SEC = 3.0

# Lifted verbatim from scripts/bridge_mvp.py: printable single-line text
# only. A raw Ctrl-C would interrupt the agent; ANSI escapes drive the TUI;
# a raw newline submits early.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b.")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Per-trace_id delivery timestamps (monotonic seconds) behind a lock. The
# lock is held ONLY for the in-memory check/record — never across a tmux
# subprocess call or a sleep.
_LOCK = threading.Lock()
_HISTORY: dict[str, deque] = defaultdict(deque)


class DeliveryResult(NamedTuple):
    delivered: bool
    detail: str


def _tmux(socket: str | None, *args: str):
    """Run a tmux command, threading `-S <socket>` when non-NULL.

    Central helper so socket threading is uniform across every call. Any
    subprocess failure (timeout, missing binary, dead socket) resolves to a
    non-zero CompletedProcess rather than an exception — the guards read
    `returncode`.
    """
    import subprocess
    cmd = ["tmux", *(["-S", socket] if socket else []), *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TMUX_TIMEOUT_SEC)
    except (subprocess.SubprocessError, OSError) as exc:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="",
                                           stderr=str(exc))


def _sanitize(text: str) -> str:
    """Printable single-line text only: no ANSI, no control bytes, no
    newlines; capped at `settings.agent_bridge.max_text_len`."""
    text = _ANSI_RE.sub("", text or "")
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = _CTRL_RE.sub("", text)
    return text[:settings.agent_bridge.max_text_len].strip()


def _rate_ok(trace_id: str) -> bool:
    """Per-trace_id sliding-window rate limit. Check/record under the lock,
    then release — no I/O or sleep is done while holding it."""
    now = time.monotonic()
    limit = settings.agent_bridge.rate_limit_per_minute
    with _LOCK:
        hist = _HISTORY[trace_id]
        while hist and now - hist[0] >= 60.0:
            hist.popleft()
        if len(hist) >= limit:
            return False
        hist.append(now)
        return True


def _match_identity(row: dict, pid_s: str, pane_pid_s: str,
                    command: str) -> str | None:
    """Refusal detail if the live pane does not match the registered triple
    (staleness + shell-execution guard), else None."""
    try:
        pid, pane_pid = int(pid_s), int(pane_pid_s)
    except ValueError:
        return "unparseable pane identity"
    if pid != row["tmux_server_pid"]:
        return (f"stale: tmux server pid {pid} != "
                f"registered {row['tmux_server_pid']}")
    if pane_pid != row["pane_pid"]:
        return f"stale: pane pid {pane_pid} != registered {row['pane_pid']}"
    if command not in settings.agent_bridge.allowed_pane_commands:
        return f"refused: pane runs {command!r}, not claude"
    return None


def _verify_identity(row: dict) -> dict:
    """Re-read the pane and confirm it is the registered claude session.

    Returns {ok, in_mode, detail}. `ok` only when the query succeeds, both
    pids match the registered triple, and the foreground command is
    allowlisted. `in_mode` (copy-mode) rides along for the cancel step.
    """
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    r = _tmux(socket, "display-message", "-p", "-t", pane,
              "#{pid}\t#{pane_pid}\t#{pane_current_command}\t#{pane_in_mode}")
    if r.returncode != 0:
        return {"ok": False, "in_mode": False,
                "detail": f"pane {pane!r} not found"}
    parts = (r.stdout or "").strip().split("\t")
    if len(parts) != 4:
        return {"ok": False, "in_mode": False,
                "detail": "unparseable pane identity"}
    in_mode = parts[3] == "1"
    refusal = _match_identity(row, parts[0], parts[1], parts[2])
    if refusal is not None:
        return {"ok": False, "in_mode": in_mode, "detail": refusal}
    return {"ok": True, "in_mode": in_mode,
            "detail": f"identity ok (command={parts[2]})"}


def _type_and_ack(row: dict, text: str, in_mode: bool) -> DeliveryResult:
    """Cancel copy-mode if needed, type the text literally, verify it landed
    in the composer (capture-pane ack), then submit. Ack failure => not
    delivered, and Enter is NOT sent."""
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    if in_mode:
        _tmux(socket, "send-keys", "-t", pane, "-X", "cancel")
        time.sleep(0.1)
    r = _tmux(socket, "send-keys", "-l", "-t", pane, "--", text)
    if r.returncode != 0:
        return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
    time.sleep(0.3)
    capture = _tmux(socket, "capture-pane", "-pt", pane, "-S", "-40")
    if text[:30] not in (capture.stdout or ""):
        return DeliveryResult(False, "typed text not visible in pane; not submitting")
    _tmux(socket, "send-keys", "-t", pane, "Enter")
    return DeliveryResult(True, f"delivered to {pane}")


def _refuse(trace_id: str, detail: str) -> DeliveryResult:
    log.write("bridge_delivery_refused", trace_id=trace_id, detail=detail)
    return DeliveryResult(False, detail)


def deliver(trace_id: str, text: str) -> DeliveryResult:
    """Resolve the reachable pane for `trace_id` and deliver `text` under the
    delivery guards. Structured refusal (never an exception) on every
    expected failure; every outcome is audited."""
    if not settings.agent_bridge.enabled:
        return _refuse(trace_id, "bridge disabled")
    if not _rate_ok(trace_id):
        return _refuse(trace_id, "rate limit exceeded")
    clean = _sanitize(text)
    if not clean:
        return _refuse(trace_id, "empty message after sanitization")
    row = store.get_reachable_pane(trace_id)
    if row is None:
        return _refuse(trace_id, "no reachable session")
    identity = _verify_identity(row)
    if not identity["ok"]:
        return _refuse(trace_id, identity["detail"])
    result = _type_and_ack(row, clean, identity["in_mode"])
    log.write("bridge_delivery_outcome", trace_id=trace_id,
              delivered=result.delivered, detail=result.detail)
    return result
