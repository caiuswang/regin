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
from lib.agent_bridge.menu_parse import parse_select_menu
from lib.settings import settings

log = get_activity_logger("agent_bridge")

# tmux subprocess guard — a hung socket must not stall the delivery path.
_TMUX_TIMEOUT_SEC = 3.0

# Lifted verbatim from scripts/bridge_mvp.py: printable single-line text
# only. A raw Ctrl-C would interrupt the agent; ANSI escapes drive the TUI;
# a raw newline submits early.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b.")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Claude's composer draws typed input inside a bordered box and SOFT-WRAPS a
# long line, injecting a newline + a `│` left-gutter mid-text. Collapsing runs
# of whitespace AND vertical box-drawing bars to one space lets the ack's
# substring match survive that reflow — a needle straddling a wrap boundary
# would otherwise never be found in the raw capture (the false "not visible"
# that stranded the typed text; see `_type_and_ack`).
_PANE_DECOR_RE = re.compile(r"[\s│┃║|]+")

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


# A pane whose identity would not parse was NOT read, however cleanly tmux
# exited: the answer is "we could not tell", and `session_liveness` must not
# turn it into the positive "gone" that settles a trace row.
_UNREADABLE = "unparseable pane identity"


def _match_identity(row: dict, pid_s: str, pane_pid_s: str,
                    command: str) -> str | None:
    """Refusal detail if the live pane does not match the registered triple
    (staleness + shell-execution guard), else None."""
    try:
        pid, pane_pid = int(pid_s), int(pane_pid_s)
    except ValueError:
        return _UNREADABLE
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

    Returns {ok, in_mode, read, detail}. `ok` only when the query succeeds,
    both pids match the registered triple, and the foreground command is
    allowlisted. `in_mode` (copy-mode) rides along for the cancel step.
    `read` says whether the pane could be QUERIED at all, which separates "we
    looked and it is not our claude" from "we could not look" — a distinction
    `ok` collapses and `session_liveness` needs.
    """
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    r = _tmux(socket, "display-message", "-p", "-t", pane,
              "#{pid}\t#{pane_pid}\t#{pane_current_command}\t#{pane_in_mode}")
    if r.returncode != 0:
        return {"ok": False, "in_mode": False, "read": False,
                "detail": f"pane {pane!r} not found"}
    parts = (r.stdout or "").strip().split("\t")
    if len(parts) != 4:
        return {"ok": False, "in_mode": False, "read": False,
                "detail": _UNREADABLE}
    in_mode = parts[3] == "1"
    refusal = _match_identity(row, parts[0], parts[1], parts[2])
    if refusal is not None:
        return {"ok": False, "in_mode": in_mode, "detail": refusal,
                "read": refusal != _UNREADABLE}
    return {"ok": True, "in_mode": in_mode, "read": True,
            "detail": f"identity ok (command={parts[2]})"}


# claude's composer can run in vim mode, and the Escape that cancels a turn
# in flight also drops it out of INSERT. Literal text typed into a NORMAL-mode
# composer is executed as motions instead: "/exit" became `/` (search), `e`,
# `x`, `i` (re-enter INSERT) and only the trailing `t` was inserted — the pane
# read `❯ t`, the ack failed, and the session never quit.
#
# The mode cannot be detected: claude renders `-- INSERT --` but shows NOTHING
# for NORMAL, which is exactly what a non-vim composer shows too. So restore it
# blind with a sequence that is correct either way — in NORMAL `i` re-enters
# INSERT, and with vim off `i` types one stray char that the trailing C-u
# removes. Both worlds end on an empty line that accepts literal text.
_COMPOSER_RESET_KEYS = ("C-u", "i", "C-u")

# The composer redraws between keys; sending them back-to-back let `i` outrun
# the C-u that was meant to precede it.
_RESET_STEP_SEC = 0.15


def _reset_composer(socket: str | None, pane: str) -> bool:
    """False when a keystroke failed to send: the reset is only empty at its
    LAST key, so a half-sent one can leave the stray `i` in the composer, and
    typing on top of that residue would submit a corrupted line."""
    sent = True
    for key in _COMPOSER_RESET_KEYS:
        if _tmux(socket, "send-keys", "-t", pane, key).returncode != 0:
            sent = False
        time.sleep(_RESET_STEP_SEC)
    return sent


# Typing a slash command opens claude's autocomplete menu, and Enter then runs
# the HIGHLIGHTED entry rather than the line you typed. Its ranking is fuzzy,
# so the highlight need not be on your command: a live run of this close path
# typed `/exit` and submitted `/python-complexity` — "compl-EXIT-y" contains
# e-x-i-t in order. A trailing space completes the command token, which
# dismisses the menu, leaving Enter nothing to do but submit the literal line.
_COMMAND_TERMINATOR = " "

# A leading slash TOKEN (slash + command chars, then whitespace or end) is the
# shape claude's composer treats as a command. A path never matches — its
# second "/" ends the token with neither whitespace nor end-of-text ("/exit"
# and "/goal fix it" match; "/Users/x/y notes" does not).
_SLASH_COMMAND_RE = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9._:-]*(?:\s|$)")


def is_slash_command(text: str) -> bool:
    """True when `text` would be read by the composer as a slash command.

    Public for the message routes: a free-form operator body of this shape
    must be delivered `as_command` or the autocomplete menu swallows the
    submitting Enter — the send acks "delivered" (the text IS visible in the
    composer) while the command never runs."""
    return bool(_SLASH_COMMAND_RE.match(text or ""))


def _type_and_ack(row: dict, text: str, in_mode: bool,
                  as_command: bool = False) -> DeliveryResult:
    """Cancel copy-mode if needed, type the text literally, verify it landed
    in the composer (capture-pane ack), then submit. Ack failure => not
    delivered, and Enter is NOT sent.

    `as_command` types `text` as a slash COMMAND rather than as a message:
    the composer is reset first (a key sent just before may have left vim's
    NORMAL mode, where literal text runs as motions) and the command token is
    terminated so the autocomplete menu cannot swallow the Enter. Reserved for
    callers that own the composer outright — the reset discards any draft a
    human had in it.
    """
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    if in_mode:
        _tmux(socket, "send-keys", "-t", pane, "-X", "cancel")
        time.sleep(0.1)
    if as_command and not _reset_composer(socket, pane):
        return DeliveryResult(False, "composer reset failed; not typing")
    r = _tmux(socket, "send-keys", "-l", "-t", pane, "--",
              f"{text}{_COMMAND_TERMINATOR}" if as_command else text)
    if r.returncode != 0:
        return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
    # Ack that the text landed in the composer before submitting. Claude's
    # echo can lag the keystroke, so POLL the pane (up to ~1.5s) rather than
    # reading once at 0.3s — a single early read was a false "not visible"
    # failure on a perfectly good send.
    needle = text[:30]
    if not _await_pane_text(socket, pane, needle):
        # The keystrokes DID land — the ack only failed to SEE them. Leaving
        # them in the composer makes the client's preserved-draft retry type
        # ON TOP, submitting duplicated or two concatenated prompts, so clear
        # the input line first. Enter is still never sent: nothing submits.
        # The full reset rather than a bare C-u: a NORMAL-mode composer ignores
        # C-u, so the residue would survive the "clear" a retry depends on.
        left = "" if _reset_composer(socket, pane) else "; composer may hold residue"
        return DeliveryResult(
            False, f"typed text not visible in pane; not submitting{left}")
    _tmux(socket, "send-keys", "-t", pane, "Enter")
    return DeliveryResult(True, f"delivered to {pane}")


def _await_pane_text(socket: str | None, pane: str, needle: str,
                     attempts: int = 5, interval: float = 0.3) -> bool:
    """Poll capture-pane until `needle` appears (echo can lag the keystroke).
    True as soon as it is seen; False if it never shows within the budget.

    Both sides are decoration-normalized (`_PANE_DECOR_RE`) so a needle broken
    by the composer's line-wrap (newline + `│` gutter) still matches — the same
    reflow-tolerant compare the multi-question focus guard uses."""
    want = _PANE_DECOR_RE.sub(" ", needle or "").strip()
    if not want:
        # A needle that is ALL whitespace/bars leaves nothing to confirm.
        # Fail CLOSED — an empty `want` would substring-match any capture and
        # submit text we never actually saw echo. (`sanitize_text` .strip()s,
        # so a real prompt never reduces to this; a bar-only prompt refuses.)
        return False
    for _ in range(attempts):
        time.sleep(interval)
        capture = _tmux(socket, "capture-pane", "-pt", pane, "-S", "-40")
        if want in _PANE_DECOR_RE.sub(" ", capture.stdout or ""):
            return True
    return False


# A multi-question ask is answered by stepping the SAME select TUI: the caller
# submits question 1, the pane renders question 2 with its cursor back at the
# top, and so on. The hazard is a silently-failed advance — if the prior Enter
# did not land, the pane is still on the earlier question, so blindly sending
# the next answer navigates the WRONG question. The stepper passes the text of
# the question it believes is focused; this confirms it is on-screen before we
# touch the arrows. Whitespace is collapsed on both sides so a terminal line-
# wrap inside the question can't defeat the substring match.
_QUESTION_NEEDLE_LEN = 40


def _pane_shows_question(socket: str | None, pane: str, text: str,
                         attempts: int = 4, interval: float = 0.25) -> bool:
    """True when `text` (whitespace-normalized) is visible in the pane.

    An empty needle confirms trivially (nothing to verify). Polls briefly
    because the next question can lag the prior submission's Enter."""
    needle = re.sub(r"\s+", " ", text or "").strip()[:_QUESTION_NEEDLE_LEN]
    if not needle:
        return True
    for i in range(attempts):
        if i:
            time.sleep(interval)
        capture = _tmux(socket, "capture-pane", "-pt", pane, "-S", "-40")
        if needle in re.sub(r"\s+", " ", capture.stdout or ""):
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
    # Same reflow-tolerant, poll-until-echoed ack `_type_and_ack` and
    # `_answer_one` use — a single early read false-negatived a good send and
    # a raw substring missed a wrapped echo.
    if not _await_pane_text(socket, pane, free_text[:30]):
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
    """Answer a pending single-question AskUserQuestion in `trace_id`'s reachable
    pane by selecting option `option_index` (0-based), or, when `free_text` is
    given, the "Type something." entry at that index typed with `free_text`. With
    `expect_chat`, `option_index` targets the "Chat about this" entry: the pane
    is first checked for that entry (refuse if a legacy build lacks it), then it
    is selected — dismissing the menu — and any `free_text` is typed into the
    reopened composer as a conversational reply. Multi-question asks go through
    `deliver_answers`. Same reachability / identity / rate guards as `deliver`;
    structured refusal (never an exception) on every expected failure; audited."""
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


def deliver_decision_option(trace_id: str, option_index: int) -> DeliveryResult:
    """Answer a pending permission/plan request whose real options are
    already known from the hook-captured `permission_suggestions` (Bash,
    Edit, and friends carry these; see `hook_manager.handlers
    .permission_events._apply_permission_info`) by selecting `option_index`
    in the reachable pane's select-TUI.

    Same primitive `deliver_answer` uses for a plain AskUserQuestion pick
    (`_navigate` from the top + Enter, no free text) — split out under its
    own name so the audit trail reads as a decision, not a question answer.
    For a request with NO structured options (today: `ExitPlanMode`), use
    `deliver_live_menu_decision` instead, which reads the real screen rather
    than trusting an assumed layout."""
    if not settings.agent_bridge.enabled:
        return _refuse(trace_id, "bridge disabled")
    if not isinstance(option_index, int) or option_index < 0 \
            or option_index > _ANSWER_MAX_NAV:
        return _refuse(trace_id, f"option index out of range: {option_index}")
    row, in_mode, refusal = _reachable_answer_pane(trace_id, expect_chat=False)
    if row is None:
        return _refuse(trace_id, refusal)
    result = _send_answer(row, option_index, None, in_mode)
    log.write("bridge_decision_outcome", trace_id=trace_id,
              option_index=option_index, delivered=result.delivered,
              detail=result.detail)
    return result


def _capture_recent(socket: str | None, pane: str, lines: int = 60) -> str:
    """Plain-text tail of `pane`'s scrollback — the raw material
    `menu_parse.parse_select_menu` reads. 60 lines comfortably covers a
    permission/plan dialog and its immediately preceding turn without
    reaching deep into an unrelated earlier screen."""
    r = _tmux(socket, "capture-pane", "-pt", pane, "-S", f"-{lines}")
    return r.stdout or ""


def _navigate_delta(socket: str | None, pane: str, steps: int) -> None:
    """Move the select-menu cursor `steps` rows from wherever it currently
    is (positive = Down, negative = Up) — unlike `_navigate`, which assumes
    the cursor starts at the top, this is given a MEASURED delta from a
    just-read cursor position, so it is safe to call after a fresh
    `parse_select_menu`."""
    key = "Down" if steps >= 0 else "Up"
    for _ in range(abs(steps)):
        _tmux(socket, "send-keys", "-t", pane, key)
        time.sleep(_NAV_STEP_SEC)


def read_live_menu(trace_id: str):
    """Read-only: the live numbered select menu on `trace_id`'s reachable
    pane right now, as a `(menu, detail)` pair — `menu` is `None` when there
    is none on screen or the capture can't be trusted (see
    `menu_parse.parse_select_menu`), in which case `detail` explains why.
    Never types or sends Enter — the peek behind the tmux-tier decide UI's
    option list, mirroring `capture_screen`'s read-only contract, but
    parsed rather than raw."""
    if not settings.agent_bridge.enabled:
        return None, "bridge disabled"
    row = store.get_reachable_pane(trace_id)
    if row is None:
        return None, "no reachable session"
    identity = _verify_identity(row)
    if not identity["ok"]:
        return None, identity["detail"]
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    menu = parse_select_menu(_capture_recent(socket, pane))
    if menu is None:
        return None, "could not reliably read a menu on screen"
    return menu, "ok"


def _menu_pick_refusal(menu, option_index: int,
                       expect_label: str | None) -> str | None:
    """Why `option_index` can't be trusted against a just-parsed `menu`, or
    `None` when it's safe to act on. `expect_label`, when given, is the
    label the operator saw and tapped at an earlier `bridge-menu` read —
    comparing it against the fresh parse closes a real TOCTOU window: a
    dialog that resolved and was replaced by a different one with at least
    `option_index + 1` rows would otherwise pass a bare range check and get
    Enter driven into it anyway."""
    if menu is None:
        return ("could not reliably read the menu on screen; "
                "resolve it in the terminal")
    if option_index >= len(menu.options):
        return (f"option index out of range: {option_index} "
                f"(menu has {len(menu.options)} options)")
    live_label = menu.options[option_index]
    if (isinstance(expect_label, str) and expect_label.strip()
            and expect_label.strip() != live_label.strip()):
        return (f"menu changed since it was read (expected "
                f"{expect_label.strip()!r}, now {live_label!r}); "
                f"resolve it in the terminal")
    return None


def deliver_live_menu_decision(trace_id: str, option_index: int,
                               expect_label: str | None = None) -> DeliveryResult:
    """Drive a numbered select menu that carries NO structured suggestion
    data (`ExitPlanMode` today — empirically verified against a live pane,
    see `menu_parse` module docstring) by reading what is actually on
    screen right now and acting on that, never on an earlier read or an
    assumed layout.

    Re-parses fresh on every call — never trusts a caller-cached menu, since
    the dialog may have resolved, reflowed, or been replaced by a different
    one since it was last read — and refuses structurally, never guesses,
    when the capture can't be trusted, `option_index` is out of range for
    what was JUST parsed, or (see `_menu_pick_refusal`) `expect_label`
    disagrees with the fresh read."""
    if not settings.agent_bridge.enabled:
        return _refuse(trace_id, "bridge disabled")
    if not isinstance(option_index, int) or option_index < 0:
        return _refuse(trace_id, f"invalid option index: {option_index}")
    row, in_mode, refusal = _reachable_answer_pane(trace_id, expect_chat=False)
    if row is None:
        return _refuse(trace_id, refusal)
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    if in_mode:
        _tmux(socket, "send-keys", "-t", pane, "-X", "cancel")
        time.sleep(0.1)
    menu = parse_select_menu(_capture_recent(socket, pane))
    pick_refusal = _menu_pick_refusal(menu, option_index, expect_label)
    if pick_refusal is not None:
        return _refuse(trace_id, pick_refusal)
    _navigate_delta(socket, pane, option_index - menu.cursor_index)
    r = _tmux(socket, "send-keys", "-t", pane, "Enter")
    if r.returncode != 0:
        return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
    detail = f"selected {menu.options[option_index]!r} in {pane}"
    log.write("bridge_live_menu_decision_outcome", trace_id=trace_id,
              option_index=option_index, delivered=True, detail=detail)
    return DeliveryResult(True, detail)


# A MULTI-question ask renders a TABBED select TUI: one tab per question plus a
# final review/Submit tab (empirically, claude v2.1.x). Selecting an option
# (Enter) checks that question's tab AND auto-advances to the next question, its
# option cursor reset to the top — so a forward walk is: for each question,
# navigate to its option and Enter; the last Enter lands on the review tab, whose
# "Submit answers" entry needs ONE more Enter to actually submit. Answering the
# last question does NOT auto-submit, so the submit step is mandatory.
_MAX_ANSWERS = 12
_SUBMIT_NEEDLE = "Submit answers"


def _answer_one(row: dict, option_index: int,
                free_text: str | None) -> DeliveryResult:
    """Select `option_index` in the CURRENTLY-focused question tab (plain pick:
    Enter; free text: type at the "Type something." entry, ack, then Enter).

    Assumes copy-mode is already cancelled and the intended tab is focused —
    `deliver_answers` guards focus per question before calling this. The select
    Enter both checks the tab and advances to the next question."""
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    _navigate(socket, pane, option_index)
    if free_text is None:
        r = _tmux(socket, "send-keys", "-t", pane, "Enter")
        if r.returncode != 0:
            return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
        return DeliveryResult(True, f"selected option {option_index + 1}")
    r = _tmux(socket, "send-keys", "-l", "-t", pane, "--", free_text)
    if r.returncode != 0:
        return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
    if not _await_pane_text(socket, pane, free_text[:30]):
        return DeliveryResult(False, "typed answer not visible in pane")
    _tmux(socket, "send-keys", "-t", pane, "Enter")
    return DeliveryResult(True, "typed answer delivered")


def _parse_one_answer(a) -> tuple | None:
    """One answer dict → (option_index, clean_text|None, confirm_text|None), or
    None if invalid."""
    if not isinstance(a, dict):
        return None
    oi = a.get("option_index")
    if not isinstance(oi, int) or oi < 0 or oi > _ANSWER_MAX_NAV:
        return None
    raw = a.get("text")
    text = None
    if raw is not None:
        text = sanitize_text(raw) if isinstance(raw, str) else ""
        if not text:
            return None
    confirm = a.get("confirm_text")
    return (oi, text, confirm if isinstance(confirm, str) else None)


def _parse_answers(answers) -> list | None:
    """Validate the ordered per-question answer list into tuples, or None if the
    shape is invalid (rejected whole, never partially applied)."""
    if not isinstance(answers, list) or not answers or len(answers) > _MAX_ANSWERS:
        return None
    out = []
    for a in answers:
        parsed = _parse_one_answer(a)
        if parsed is None:
            return None
        out.append(parsed)
    return out


def _walk_answers(row: dict, parsed: list) -> DeliveryResult:
    """Select each question's option in order, guarding that the expected
    question is the focused tab before each — the select Enter checks the tab
    and auto-advances. Stops (structured) at the first desync/failure."""
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    for i, (option_index, text, confirm) in enumerate(parsed):
        if confirm and not _pane_shows_question(socket, pane, confirm):
            return DeliveryResult(False, f"question {i + 1} not focused in pane")
        step = _answer_one(row, option_index, text)
        if not step.delivered:
            return DeliveryResult(False, f"question {i + 1}: {step.detail}")
    return DeliveryResult(True, "all questions answered")


def deliver_answers(trace_id: str, answers) -> DeliveryResult:
    """Answer a MULTI-question AskUserQuestion by walking its tabbed select TUI.

    `answers` is the ordered per-question list ({option_index, text?,
    confirm_text?}). Each question is answered in order (focus-guarded); the
    select Enter checks that tab and advances. After the last, the review/Submit
    tab is verified and Enter'd to submit. Refuses (structured, never raising) on
    any desync, so a stuck walk can neither answer the wrong tab nor submit a
    half-filled form. Same reachability / identity / rate guards as `deliver`;
    audited."""
    if not settings.agent_bridge.enabled:
        return _refuse(trace_id, "bridge disabled")
    parsed = _parse_answers(answers)
    if parsed is None:
        return _refuse(trace_id, "invalid answers")
    row, in_mode, refusal = _reachable_answer_pane(trace_id, False)
    if row is None:
        return _refuse(trace_id, refusal)
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    if in_mode:
        _tmux(socket, "send-keys", "-t", pane, "-X", "cancel")
        time.sleep(0.1)
    walk = _walk_answers(row, parsed)
    if not walk.delivered:
        return _refuse(trace_id, walk.detail)
    if not _pane_shows_question(socket, pane, _SUBMIT_NEEDLE):
        return _refuse(trace_id, "submit screen not shown; answers not submitted")
    _tmux(socket, "send-keys", "-t", pane, "Enter")
    log.write("bridge_answers_outcome", trace_id=trace_id, count=len(parsed),
              delivered=True, detail=f"submitted to {pane}")
    return DeliveryResult(True, f"submitted {len(parsed)} answers to {pane}")


def deliver(trace_id: str, text: str,
            as_command: bool = False) -> DeliveryResult:
    """Resolve the reachable pane for `trace_id` and deliver `text` under the
    delivery guards. Structured refusal (never an exception) on every
    expected failure; every outcome is audited.

    `as_command` sends `text` as a slash command instead of as a message —
    see `_COMPOSER_RESET_KEYS` and `_COMMAND_TERMINATOR` for the two ways a
    naively typed command misses. Off for ordinary steering, whose composer
    may hold a human's draft the reset would discard.
    """
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
    result = _type_and_ack(row, clean, identity["in_mode"], as_command)
    log.write("bridge_delivery_outcome", trace_id=trace_id,
              delivered=result.delivered, detail=result.detail)
    return result


# Liveness has three answers, not two. Collapsing the last two into False is
# right for a gate that must fail closed (a resume refused on no evidence
# costs little), and WRONG for confirming a shutdown: "the probe broke" would
# read as "the process exited", settling the trace row while the agent keeps
# running — the very bug the confirmation exists to catch.
LIVE = "live"
GONE = "gone"
UNKNOWN = "unknown"


def session_liveness(trace_id: str) -> str:
    """LIVE / GONE / UNKNOWN for `trace_id`'s registered pane.

    GONE is a POSITIVE reading: the pane was queried and something other than
    the registered claude holds it. Everything that merely prevented the
    question from being answered — bridge off, no row, a tmux call that
    failed or timed out — is UNKNOWN.
    """
    if not settings.agent_bridge.enabled:
        return UNKNOWN
    row = store.get_reachable_pane(trace_id)
    if row is None:
        return UNKNOWN
    identity = _verify_identity(row)
    if identity["ok"]:
        return LIVE
    return GONE if identity.get("read") else UNKNOWN


def session_is_live(trace_id: str) -> bool:
    """True when `trace_id` is the session a terminal is driving right now.

    The resume gate: reopening a conversation a live CLI still holds puts two
    processes on one session id — and, because a resume claims the id for the
    run, redirects the real session's own composer at the copy.

    The registry alone cannot answer it. A pane whose claude has exited keeps
    its row until the next session claims the pane, so a row means "this
    session was last in that pane", not "it is still running". The foreground
    command is therefore re-read — the same evidence `deliver` demands before
    it types anything.

    False when the bridge is off: with no registry there is no evidence either
    way, and refusing every resume on a hunch costs more than it saves.
    """
    return session_liveness(trace_id) == LIVE


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
