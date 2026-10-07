"""tmux pane primitives shared by the bridge's delivery paths: the guarded
`tmux` call, pane identity, the per-trace rate limit, body sanitising, and
the composer reset / echo-ack helpers.
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict, deque
from typing import NamedTuple

from lib.activity_log import get_activity_logger
from lib.settings import settings


log = get_activity_logger("agent_bridge")


# tmux subprocess guard — a hung socket must not stall the delivery path.
_TMUX_TIMEOUT_SEC = 3.0


# Printable text only: a raw Ctrl-C would interrupt the agent and ANSI escapes
# drive the TUI. The sweep spares TAB (0x09) and LF (0x0a) — the line structure
# of a pasted diff, stack trace or source file IS the message, and the bracketed
# paste below carries it without submitting the half-typed line.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b.")


_CTRL_KEEP_BREAKS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


_BREAK_RE = re.compile(r"[\n\t]")


# Claude's composer runs in bracketed-paste mode, and that is the ONLY input
# mode that survives a body the terminal cannot take in one burst: a single
# `send-keys -l` of a 1448-byte prompt reached the composer as a byte-exact
# SUFFIX FROM OFFSET 1024 — the head was silently gone and the send still
# ACKed, because the surviving tail is what the echo matched (trace aac4dd29,
# bridge_messages 917/918). Chunking the same literal send does not fix it;
# the markers do, byte-exactly, up to `max_text_len` (probed live against
# Claude Code v2.1.259). Inside them a newline INSERTS rather than submits.
_PASTE_START, _PASTE_END = "\x1b[200~", "\x1b[201~"


# Claude's composer draws typed input inside a bordered box and SOFT-WRAPS a
# long line, injecting a newline + a `│` left-gutter mid-text. Collapsing runs
# of whitespace AND vertical box-drawing bars to one space lets the ack's
# substring match survive that reflow — a needle straddling a wrap boundary
# would otherwise never be found in the raw capture (the false "not visible"
# that stranded the typed text; see `_type_and_ack`).
_PANE_DECOR_RE = re.compile(r"[\s│┃║|]+")


# Past roughly 880 characters the composer stops rendering the input at all and
# draws a `[Pasted text #N]` chip in its place (measured on Claude Code
# v2.1.252: the flip is at 886 chars on a 137-column pane, 864 on a 159-column
# one). The literal-echo ack then CANNOT match, so a long prompt was refused
# nine times running while its text sat correctly in the composer.
#
# The chip's counter is monotonic for the life of the claude process — it does
# not reset on `/clear` — so a chip numbered ABOVE the highest one present
# before we typed is proof that OUR burst reached the composer. A pre-existing
# chip (a human's staged paste, or one still in scrollback from an earlier
# turn) is not, which is why the baseline is read rather than assumed zero.
#
# A MULTI-LINE paste draws `[Pasted text #2 +48 lines]`, not the bare
# `[Pasted text #2]` of a single-line one, so BOTH closing forms are spelled
# out — matching the single-line one alone failed the ack on every pasted
# diff/source body and cleared it unsent. Still terminated, so prose that
# merely quotes a chip ("the log said [Pasted text #99 was odd") cannot poison
# the baseline to a number the real counter will never exceed.
_PASTE_CHIP_RE = re.compile(r"\[Pasted text #(\d+)(?:\s+\+\d+\s+lines?)?\s*\]")


def _paste_chip_high_water(capture: str,
                           chip: re.Pattern = _PASTE_CHIP_RE) -> int:
    """Highest `[Pasted text #N]` counter anywhere in `capture`; 0 for none.

    Normalizes first, and does so HERE rather than at the call sites: reading
    the pre-type baseline raw while the ack reads it decoration-normalized lets
    a soft-wrapped STALE chip go unseen in the baseline (0) and seen after the
    send (`5 > 0`) — a blind submit of text that never landed, which is exactly
    what the baseline exists to prevent."""
    return max((int(n) for n in
                chip.findall(_PANE_DECOR_RE.sub(" ", capture or ""))),
               default=0)


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


def sanitize_body(text: str, capped: bool = True) -> str:
    """Printable text with its LINE STRUCTURE intact: no ANSI, no control
    bytes but tab and newline, `\r\n`/`\r` normalized to `\n`; capped at
    `settings.agent_bridge.max_text_len`.

    The sanitizer for anything typed into the composer as a MESSAGE. Flattening
    a prompt to one line is not a safety property, it is data loss — the same
    reasoning `_sdk_prompt_text` already applies to the queue path, now that
    `_PASTE_START` lets the tmux path carry a newline without submitting on it.

    Public so the HTTP surface can bound/clean the STORED body (the inbox row)
    with the same rule delivery types with — one sanitizer, no drift.
    Idempotent: re-sanitizing already-clean text is a no-op."""
    text = _ANSI_RE.sub("", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CTRL_KEEP_BREAKS_RE.sub("", text)
    if capped:
        text = text[:settings.agent_bridge.max_text_len]
    return text.strip()


def sanitize_text(text: str) -> str:
    """`sanitize_body` flattened to a single line.

    For text that must not carry a line break at all: a slash command, a menu
    label, a decision reason, an answer. Keeps the `.strip()` that lets
    `is_slash_command` anchor at the first character."""
    return _BREAK_RE.sub(" ", sanitize_body(text)).strip()


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


def allowed_pane_commands() -> frozenset[str]:
    """The foreground commands a pane may run and still be steered: the
    operator's list plus what each provider declares its CLI reports."""
    from lib.providers.registry import declared_pane_commands

    return (frozenset(settings.agent_bridge.allowed_pane_commands)
            | declared_pane_commands())


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
    if command not in allowed_pane_commands():
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
#
# Because the sequence CLEARS the line, it long ran only for commands, whose
# caller owns the composer. That left ordinary steering exposed to the same
# NORMAL mode, and the loss there is silent rather than loud: a body opening
# with `S` (substitute-line, which itself enters INSERT) lost exactly that one
# character, the ack matched on the tail, and the agent answered a prompt
# missing its first letter. An EMPTY composer has no draft to protect, so the
# reset is safe there too — `_composer_is_empty` is what separates the two.
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
_MENU_TERMINATOR = " "


# The same swallowed-Enter hazard as the slash menu above, at the other end of
# the line: a trailing `@token` leaves claude's FILE-MENTION picker open, and
# Enter then inserts the highlighted path instead of submitting. Probed live in
# a scratch pane — `hello @/Users/taowang/regin/` held the picker open over the
# composer; one space closed it and Enter submitted with the mention still
# resolved ("Listed directory .../regin/"). Only a token at END of text can eat
# the Enter; a mention mid-message was already closed by the space after it.
_MENTION_TAIL_RE = re.compile(r"(?:^|\s)@\S*$")


def ends_with_mention(text: str) -> bool:
    """True when `text` would leave the file-mention picker open at the cursor.

    An operator body of this shape acks "delivered" (the text IS in the
    composer) while nothing was ever submitted — trace 212ebe7f sat on two such
    sends, silent, with the picker still open. Public for the answer paths,
    which type into the same composer without going through `_type_and_ack`."""
    return bool(_MENTION_TAIL_RE.search(text or ""))


def _typed_payload(text: str, as_command: bool) -> str:
    """The literal keystrokes to send: `text`, terminated when it would leave
    an autocomplete menu open under the cursor."""
    if as_command or ends_with_mention(text):
        return f"{text}{_MENU_TERMINATOR}"
    return text


def _pasted_payload(text: str) -> str:
    """`text` wrapped in bracketed-paste markers — how composer MESSAGES are
    sent (`_PASTE_START`). A slash command keeps the plain literal path: it is
    one short line the burst limit cannot reach, and it is typed precisely so
    the autocomplete menu opens for `_MENU_TERMINATOR` to close."""
    return f"{_PASTE_START}{_typed_payload(text, False)}{_PASTE_END}"


def _composer_ack_refusal(socket: str | None, pane: str, text: str,
                          baseline: int | None,
                          chip: re.Pattern = _PASTE_CHIP_RE) -> str | None:
    """None once the composer has echoed `text`; else the refusal detail.

    The composer renders a long line three ways and only one of them shows the
    head, so all three signals go to `_await_pane_text`. On failure the
    keystrokes DID land — the ack only failed to SEE them — and leaving them
    makes the client's preserved-draft retry type ON TOP, submitting two
    concatenated prompts; so the line is cleared first. Enter is never sent
    either way: nothing submits. The full reset rather than a bare C-u because
    a NORMAL-mode composer ignores C-u, and the residue would survive the
    "clear" a retry depends on."""
    if _await_pane_text(socket, pane, text[:30], tail=text[-30:],
                        paste_baseline=baseline, chip=chip):
        return None
    left = "" if _reset_composer(socket, pane) else "; composer may hold residue"
    return f"not visible in pane; not submitting{left}"


def _echo_seen(raw: str, wants: list[str], paste_baseline: int | None,
               chip: re.Pattern = _PASTE_CHIP_RE) -> bool:
    """True when `raw` shows any of `wants`, or a paste chip newer than
    `paste_baseline`. See `_await_pane_text` for why one needle is not enough."""
    if any(w in _PANE_DECOR_RE.sub(" ", raw) for w in wants):
        return True
    return (paste_baseline is not None
            and _paste_chip_high_water(raw, chip) > paste_baseline)


def _await_pane_text(socket: str | None, pane: str, needle: str,
                     attempts: int = 5, interval: float = 0.3,
                     tail: str | None = None,
                     paste_baseline: int | None = None,
                     chip: re.Pattern = _PASTE_CHIP_RE) -> bool:
    """Poll capture-pane until `needle` appears (echo can lag the keystroke).
    True as soon as it is seen; False if it never shows within the budget.

    Both sides are decoration-normalized (`_PANE_DECOR_RE`) so a needle broken
    by the composer's line-wrap (newline + `│` gutter) still matches — the same
    reflow-tolerant compare the multi-question focus guard uses.

    A long line has THREE renderings and `needle` (the head of the text) only
    survives one of them, which is why the other two are opt-in here:

      * soft-wrapped across the box   → the head is on screen; `needle` matches
      * horizontally scrolled         → only the END is on screen; `tail` does
      * collapsed to `[Pasted text #N]` → neither is; `paste_baseline` does

    So a composer caller passes all three. Callers that type somewhere other
    than the composer — a menu's free-text entry, which is short and neither
    scrolls nor collapses — pass only `needle` and keep the strict rule.
    """
    head = _PANE_DECOR_RE.sub(" ", needle or "").strip()
    if not head:
        # A needle that is ALL whitespace/bars leaves nothing to confirm.
        # Fail CLOSED — an empty `head` would substring-match any capture and
        # submit text we never actually saw echo. (`sanitize_text` .strip()s,
        # so a real prompt never reduces to this; a bar-only prompt refuses.)
        return False
    wants = [head]
    tail_want = _PANE_DECOR_RE.sub(" ", tail or "").strip()
    if tail_want:
        wants.append(tail_want)
    for _ in range(attempts):
        time.sleep(interval)
        capture = _tmux(socket, "capture-pane", "-pt", pane, "-S", "-40")
        if _echo_seen(capture.stdout or "", wants, paste_baseline, chip):
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


def _refuse(trace_id: str, detail: str) -> DeliveryResult:
    log.write("bridge_delivery_refused", trace_id=trace_id, detail=detail)
    return DeliveryResult(False, detail)
