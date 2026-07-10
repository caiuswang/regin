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
from collections import OrderedDict, deque
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
# subprocess call or a sleep. Bounded two ways so an authed caller spraying
# distinct trace_ids can't grow it without limit: a window that empties drops
# its key, and the total tracked ids are LRU-capped.
_LOCK = threading.Lock()
_HISTORY: "OrderedDict[str, deque]" = OrderedDict()
_MAX_TRACKED_TRACES = 4096


class DeliveryResult(NamedTuple):
    delivered: bool
    detail: str


class CaptureResult(NamedTuple):
    ok: bool
    text: str
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


def sanitize_text(text: str) -> str:
    """Printable single-line text only: no ANSI, no control bytes, no
    newlines; capped at `settings.agent_bridge.max_text_len`.

    Public so the HTTP surface can bound/clean the STORED body (the inbox
    row) with the same rule delivery applies to the typed copy — one
    sanitizer, no drift. Idempotent: re-sanitizing already-clean text is a
    no-op, so the view may store the cleaned text and still pass it to
    deliver()."""
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
        hist = _HISTORY.get(trace_id) or deque()
        while hist and now - hist[0] >= 60.0:
            hist.popleft()
        allowed = len(hist) < limit
        if allowed:
            hist.append(now)
        if hist:
            _HISTORY[trace_id] = hist
            _HISTORY.move_to_end(trace_id)
            while len(_HISTORY) > _MAX_TRACKED_TRACES:
                _HISTORY.popitem(last=False)
        else:
            _HISTORY.pop(trace_id, None)
        return allowed


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
    # Ack that the text landed in the composer before submitting. Claude's
    # echo can lag the keystroke, so POLL the pane (up to ~1.5s) rather than
    # reading once at 0.3s — a single early read was a false "not visible"
    # failure on a perfectly good send.
    needle = text[:30]
    if not _await_pane_text(socket, pane, needle):
        return DeliveryResult(False, "typed text not visible in pane; not submitting")
    _tmux(socket, "send-keys", "-t", pane, "Enter")
    return DeliveryResult(True, f"delivered to {pane}")


def _await_pane_text(socket: str | None, pane: str, needle: str,
                     attempts: int = 5, interval: float = 0.3) -> bool:
    """Poll capture-pane until `needle` appears (echo can lag the keystroke).
    True as soon as it is seen; False if it never shows within the budget."""
    for _ in range(attempts):
        time.sleep(interval)
        capture = _tmux(socket, "capture-pane", "-pt", pane, "-S", "-40")
        if needle in (capture.stdout or ""):
            return True
    return False


# Named keys the bridge may inject as a RAW keystroke (no literal text, no
# trailing Enter, no composer ack). Escape is the recovery key: a harness
# overlay (slash-command help, a menu) swallows the composer's typed text so
# a normal send fails its ack ("typed text not visible") — one Escape
# dismisses the overlay from mobile so typing works again. Allowlisted so the
# key path can never be coerced into an arbitrary control sequence.
_ALLOWED_KEYS = {"Escape"}


def _send_key(row: dict, key: str) -> DeliveryResult:
    """Inject a single named key into the pane (no ack — a keystroke leaves
    no reliable capture-pane trace like typed text does)."""
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    r = _tmux(socket, "send-keys", "-t", pane, key)
    if r.returncode != 0:
        return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
    return DeliveryResult(True, f"{key} sent to {pane}")


def deliver_key(trace_id: str, key: str) -> DeliveryResult:
    """Inject an allowlisted named key into `trace_id`'s reachable pane under
    the same reachability / identity / rate guards as `deliver()`. Structured
    refusal (never an exception) on every expected failure; audited."""
    if not settings.agent_bridge.enabled:
        return _refuse(trace_id, "bridge disabled")
    if key not in _ALLOWED_KEYS:
        return _refuse(trace_id, f"unsupported key {key!r}")
    if not _rate_ok(trace_id):
        return _refuse(trace_id, "rate limit exceeded")
    row = store.get_reachable_pane(trace_id)
    if row is None:
        return _refuse(trace_id, "no reachable session")
    identity = _verify_identity(row)
    if not identity["ok"]:
        return _refuse(trace_id, identity["detail"])
    result = _send_key(row, key)
    log.write("bridge_key_outcome", trace_id=trace_id, key=key,
              delivered=result.delivered, detail=result.detail)
    return result


def _refuse(trace_id: str, detail: str) -> DeliveryResult:
    log.write("bridge_delivery_refused", trace_id=trace_id, detail=detail)
    return DeliveryResult(False, detail)


# An AskUserQuestion is answered by driving its select TUI: the cursor starts
# on the first option (index 0), so option `i` is reached with Down×i then
# Enter — one Enter, one submission, deterministic regardless of number-key
# semantics. The auto-appended "Type something." free-text entry sits at index
# = the number of listed options; selecting it opens a text field we then type
# into and ack (like `deliver`) before the final Enter. The "Chat about this"
# entry (below the TUI's divider) sits one past that (index = options + 1):
# selecting it dismisses the menu back to the composer, where an optional
# message is typed as a conversational reply. Bound the walk so a bad index
# can't spin the arrow loop.
_ANSWER_MAX_NAV = 50
_NAV_STEP_SEC = 0.03
_CHAT_ENTRY_LABEL = "Chat about this"


def _chat_entry_present(socket: str | None, pane: str) -> bool:
    """True when the pane's live menu shows a 'Chat about this' entry.

    Guards the chat verb against a claude build that predates the entry: over
    such a version the frontend's chat index would over-navigate the menu, so
    refuse rather than answer the wrong entry."""
    r = _tmux(socket, "capture-pane", "-pt", pane, "-S", "-40")
    return _CHAT_ENTRY_LABEL in (r.stdout or "")


def _navigate(socket: str | None, pane: str, steps: int) -> None:
    """Move the AskUserQuestion cursor `steps` options down from the top."""
    for _ in range(steps):
        _tmux(socket, "send-keys", "-t", pane, "Down")
        time.sleep(_NAV_STEP_SEC)


def _send_answer(row: dict, option_index: int, free_text: str | None,
                 in_mode: bool, is_chat: bool = False) -> DeliveryResult:
    """Drive the ask's select TUI to option `option_index` then submit.

    The three verbs need different key sequences (empirically, claude v2.1.x):
    - plain pick / `free_text is None`: Enter selects (or, at the chat entry,
      dismisses the menu). Best-effort — the menu vanishes, no capture trace.
    - "Type something." (`free_text`, not chat): the entry becomes an INLINE
      field on the FIRST keystroke — typing directly rewrites the label. An
      Enter *before* typing here DECLINES the question, so we must NOT open it
      with Enter; type, ack, then Enter submits the custom answer.
    - "Chat about this" (`free_text` + `is_chat`): Enter first DISMISSES the
      menu into the composer, then the message is typed and Enter submits it.
    """
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    if in_mode:
        _tmux(socket, "send-keys", "-t", pane, "-X", "cancel")
        time.sleep(0.1)
    _navigate(socket, pane, option_index)
    if free_text is None:
        r = _tmux(socket, "send-keys", "-t", pane, "Enter")
        if r.returncode != 0:
            return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
        return DeliveryResult(True, f"selected option {option_index + 1} in {pane}")
    if is_chat:
        # Chat: this Enter dismisses the menu into the composer before typing.
        _tmux(socket, "send-keys", "-t", pane, "Enter")
        time.sleep(0.1)
    r = _tmux(socket, "send-keys", "-l", "-t", pane, "--", free_text)
    if r.returncode != 0:
        return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
    # Ack the typed text landed before submitting (same capture-pane check
    # `_type_and_ack` applies to a steering message).
    time.sleep(0.3)
    capture = _tmux(socket, "capture-pane", "-pt", pane, "-S", "-40")
    if free_text[:30] not in (capture.stdout or ""):
        return DeliveryResult(False, "typed answer not visible in pane; not submitting")
    _tmux(socket, "send-keys", "-t", pane, "Enter")
    kind = "chat message" if is_chat else "typed answer"
    return DeliveryResult(True, f"{kind} delivered to {pane}")


def _reachable_answer_pane(trace_id: str, expect_chat: bool):
    """Shared answer preflight: rate limit, reachable pane, identity, and the
    chat-entry presence check. Returns (row, in_mode, refusal_detail); row is
    None when refused (refusal_detail set), else refusal_detail is ""."""
    if not _rate_ok(trace_id):
        return None, False, "rate limit exceeded"
    row = store.get_reachable_pane(trace_id)
    if row is None:
        return None, False, "no reachable session"
    identity = _verify_identity(row)
    if not identity["ok"]:
        return None, False, identity["detail"]
    if expect_chat and not _chat_entry_present(row.get("tmux_socket"),
                                               row["pane_id"]):
        return None, False, "no 'Chat about this' entry in menu"
    return row, identity["in_mode"], ""


def deliver_answer(trace_id: str, option_index: int,
                   free_text: str | None = None,
                   expect_chat: bool = False) -> DeliveryResult:
    """Answer a pending AskUserQuestion in `trace_id`'s reachable pane by
    selecting option `option_index` (0-based), or, when `free_text` is given,
    the "Type something." entry at that index typed with `free_text`. With
    `expect_chat`, `option_index` targets the "Chat about this" entry: the pane
    is first checked for that entry (refuse if a legacy build lacks it), then it
    is selected — dismissing the menu — and any `free_text` is typed into the
    reopened composer as a conversational reply. Same reachability / identity /
    rate guards as `deliver`; structured refusal (never an exception) on every
    expected failure; audited."""
    if not settings.agent_bridge.enabled:
        return _refuse(trace_id, "bridge disabled")
    if not isinstance(option_index, int) or option_index < 0 \
            or option_index > _ANSWER_MAX_NAV:
        return _refuse(trace_id, f"option index out of range: {option_index}")
    clean = None
    if free_text is not None:
        clean = sanitize_text(free_text)
        if not clean:
            return _refuse(trace_id, "empty answer after sanitization")
    row, in_mode, refusal = _reachable_answer_pane(trace_id, expect_chat)
    if row is None:
        return _refuse(trace_id, refusal)
    result = _send_answer(row, option_index, clean, in_mode, is_chat=expect_chat)
    log.write("bridge_answer_outcome", trace_id=trace_id,
              option_index=option_index, free_text=clean is not None,
              chat=expect_chat, delivered=result.delivered, detail=result.detail)
    return result


def deliver(trace_id: str, text: str) -> DeliveryResult:
    """Resolve the reachable pane for `trace_id` and deliver `text` under the
    delivery guards. Structured refusal (never an exception) on every
    expected failure; every outcome is audited."""
    if not settings.agent_bridge.enabled:
        return _refuse(trace_id, "bridge disabled")
    if not _rate_ok(trace_id):
        return _refuse(trace_id, "rate limit exceeded")
    clean = sanitize_text(text)
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


def capture_screen(trace_id: str, lines: int | None = None) -> CaptureResult:
    """Read-only `capture-pane` snapshot of `trace_id`'s reachable pane.

    Default (`lines=None`) captures just the pane's CURRENT visible screen —
    omitting `-S` entirely, not `-S -<large N>` — since that's what a "peek
    at the real terminal" question actually wants; scrollback is opt-in via
    an explicit `lines` depth. Same reachability/identity guards as
    `deliver()` — refuses a stale or non-claude pane rather than silently
    reading whatever now occupies a recycled pane id — but never types or
    sends Enter. `text` carries the raw SGR/256-color escape codes (`-e`);
    callers convert to HTML for display. Structured refusal (never an
    exception) on every expected failure; audited like every other bridge
    outcome.
    """
    if not settings.agent_bridge.enabled:
        return CaptureResult(False, "", "bridge disabled")
    row = store.get_reachable_pane(trace_id)
    if row is None:
        return CaptureResult(False, "", "no reachable session")
    identity = _verify_identity(row)
    if not identity["ok"]:
        return CaptureResult(False, "", identity["detail"])
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    args = ["capture-pane", "-t", pane, "-p", "-e"]
    if lines:
        args += ["-S", f"-{lines}"]
    r = _tmux(socket, *args)
    if r.returncode != 0:
        detail = f"capture-pane failed: {r.stderr.strip()}"
        log.write("bridge_capture_outcome", trace_id=trace_id, ok=False, detail=detail)
        return CaptureResult(False, "", detail)
    log.write("bridge_capture_outcome", trace_id=trace_id, ok=True,
              detail=f"captured {pane}")
    return CaptureResult(True, r.stdout, f"captured {pane}")
