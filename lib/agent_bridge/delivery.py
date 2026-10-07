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
import time
from typing import TYPE_CHECKING, NamedTuple

from lib.agent_bridge import mod_channel, store
from lib.agent_bridge.answer_delivery import (
    _toggle_multi,
    deliver_answer,
    deliver_answers,
    deliver_decision_option,
    deliver_live_menu_decision,
    read_live_menu,
)
from lib.agent_bridge.menu_parse import parse_select_menu
from lib.agent_bridge.pane_io import (
    _COMPOSER_RESET_KEYS,
    _HISTORY,
    _TMUX_TIMEOUT_SEC,
    DeliveryResult,
    _composer_ack_refusal,
    _paste_chip_high_water,
    _pasted_payload,
    _rate_ok,
    _refuse,
    _reset_composer,
    _tmux,
    _typed_payload,
    _verify_identity,
    ends_with_mention,
    log,
    sanitize_body,
    sanitize_text,
)
from lib.settings import settings

if TYPE_CHECKING:
    from lib.providers.base import PaneInput


class CaptureResult(NamedTuple):
    ok: bool
    text: str
    detail: str


def _composer_is_empty(capture) -> bool:
    """True only when `capture` plainly shows a composer holding no draft.

    Fails closed: an unreadable capture, or a screen `parse_screen` will not
    vouch for, answers False — the reset this gates clears the line, so
    "don't know" has to mean "leave the operator's draft alone".
    """
    from lib.agent_bridge import screen_state
    if capture.returncode != 0:
        return False
    state = screen_state.parse_screen(capture.stdout)
    return state is not None and not state.draft


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

    The composer is reset out of a possible vim NORMAL mode (where literal
    text runs as motions) before typing — always for `as_command`, which owns
    the composer outright, and otherwise only when the composer is provably
    empty, since the reset discards any draft a human had in it.

    Text ending in an `@mention` is terminated the same way `as_command` is,
    for the same reason at the other end of the line: the file picker it leaves
    open would otherwise eat the submitting Enter (`ends_with_mention`).

    `as_command` types `text` as a slash COMMAND rather than as a message: the
    command token is terminated so the autocomplete menu cannot swallow the
    Enter.
    """
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    if in_mode:
        _tmux(socket, "send-keys", "-t", pane, "-X", "cancel")
        time.sleep(0.1)
    # A baseline read that FAILED is unknown, not zero. `_tmux` flattens every
    # failure (timeout, dead socket) to rc=1 with empty stdout, and a zero
    # baseline would promote any stale chip already in the window to "newer
    # than before we typed" — submitting text that never reached the composer.
    # Unknown turns the chip rule off and falls back to the literal echo.
    # One read serves both the baseline and the emptiness probe below: it has
    # to precede the typing either way, and a second round-trip per steer buys
    # nothing (the reset only ever runs on a composer this read saw empty).
    baseline_read = _tmux(socket, "capture-pane", "-pt", pane, "-S", "-40")
    baseline = (_paste_chip_high_water(baseline_read.stdout)
                if baseline_read.returncode == 0 else None)
    if ((as_command or _composer_is_empty(baseline_read))
            and not _reset_composer(socket, pane)):
        return DeliveryResult(False, "composer reset failed; not typing")
    r = _tmux(socket, "send-keys", "-l", "-t", pane, "--",
              _typed_payload(text, True) if as_command else _pasted_payload(text))
    if r.returncode != 0:
        return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
    # Ack that the text landed in the composer before submitting. Claude's
    # echo can lag the keystroke, so POLL the pane (up to ~1.5s) rather than
    # reading once at 0.3s — a single early read was a false "not visible"
    # failure on a perfectly good send.
    refusal = _composer_ack_refusal(socket, pane, text, baseline)
    if refusal:
        return DeliveryResult(False, f"typed text {refusal}")
    _tmux(socket, "send-keys", "-t", pane, "Enter")
    return DeliveryResult(True, f"delivered to {pane}")


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


def deliver(trace_id: str, text: str,
            as_command: bool = False) -> DeliveryResult:
    """Deliver `text` to `trace_id` under the delivery guards: a message goes
    through the session's connected Mod listener when it has one, else (and
    always for a command) into its reachable tmux pane. Structured refusal (never an exception) on every
    expected failure; every outcome is audited.

    `as_command` sends `text` as a slash command instead of as a message —
    see `_COMPOSER_RESET_KEYS` and `_MENU_TERMINATOR` for the two ways a
    naively typed command misses. Off for ordinary steering, whose composer
    may hold a human's draft the reset would discard.
    """
    if not settings.agent_bridge.enabled:
        return _refuse(trace_id, "bridge disabled")
    if not _rate_ok(trace_id):
        return _refuse(trace_id, "rate limit exceeded")
    clean = sanitize_text(text) if as_command else sanitize_body(text)
    if not clean:
        return _refuse(trace_id, "empty message after sanitization")
    # A slash command stays on tmux: whether `$.prompt.submit` runs one as a
    # command rather than sending its text to the model is not yet verified.
    if not as_command and mod_channel.offer(trace_id, clean):
        result = DeliveryResult(True, "delivered via mod listener")
        log.write("bridge_delivery_outcome", trace_id=trace_id,
                  delivered=True, detail=result.detail, channel="mod")
        return result
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


# The probed kimi drew its composer ~1.7s after spawn; MCP servers it connects
# at startup add to that. Past the budget the turn is refused, never typed blind.
_READY_ATTEMPTS = 60
_READY_INTERVAL_SEC = 0.25


def _await_composer(socket: str | None, pane: str,
                    ready: re.Pattern) -> str | None:
    """The first capture showing the composer, or None if none ever does."""
    for attempt in range(_READY_ATTEMPTS):
        if attempt:
            time.sleep(_READY_INTERVAL_SEC)
        capture = _tmux(socket, "capture-pane", "-pt", pane, "-S", "-40")
        if capture.returncode == 0 and ready.search(capture.stdout or ""):
            return capture.stdout
    return None


def deliver_first_turn(trace_id: str, text: str,
                       typing: "PaneInput") -> DeliveryResult:
    """Type a just-launched pane session's first turn, for a CLI whose argv
    cannot carry one, the way `typing` says its composer takes input.

    Uncapped, unlike `deliver`: this is a task prompt, whose finish
    instruction comes last, not an operator message. Refused (never raised)
    when the pane is not the registered CLI, the composer never draws, or the
    paste is not seen landing — and then nothing is submitted.
    """
    row = store.get_reachable_pane(trace_id)
    if row is None:
        return _refuse(trace_id, "no reachable session")
    identity = _verify_identity(row)
    if not identity["ok"]:
        return _refuse(trace_id, identity["detail"])
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    screen = _await_composer(socket, pane, re.compile(typing.ready))
    if screen is None:
        return _refuse(trace_id, "composer never appeared")
    chip = re.compile(typing.paste_chip)
    clean = sanitize_body(text, capped=False)
    r = _tmux(socket, "send-keys", "-l", "-t", pane, "--",
              _pasted_payload(clean))
    if r.returncode != 0:
        return _refuse(trace_id, f"send-keys failed: {r.stderr.strip()}")
    refusal = _composer_ack_refusal(socket, pane, clean,
                                    _paste_chip_high_water(screen, chip), chip)
    if refusal:
        return _refuse(trace_id, f"typed text {refusal}")
    r = _tmux(socket, "send-keys", "-t", pane, typing.submit_key)
    if r.returncode != 0:
        return _refuse(trace_id, f"submit failed: {r.stderr.strip()}")
    result = DeliveryResult(True, f"delivered to {pane}")
    log.write("bridge_delivery_outcome", trace_id=trace_id, delivered=True,
              detail=result.detail, op="first_turn")
    return result


def send_now(trace_id: str) -> DeliveryResult:
    """Abort `trace_id`'s running turn through its Mod listener, so Claude Code
    goes on to the prompts queued behind it.

    Mod channel only: tmux's Escape would pull the queued prompts back into the
    composer instead of running them, so no listener means a refusal.
    """
    if not settings.agent_bridge.enabled:
        return _refuse(trace_id, "bridge disabled")
    if not _rate_ok(trace_id):
        return _refuse(trace_id, "rate limit exceeded")
    if not mod_channel.offer_control(trace_id, "abort"):
        return _refuse(trace_id, "no mod listener")
    result = DeliveryResult(True, "abort sent via mod listener")
    log.write("bridge_delivery_outcome", trace_id=trace_id, delivered=True,
              detail=result.detail, channel="mod", op="abort")
    return result


# Liveness has three answers, not two. Collapsing the last two into False is
# right for a gate that must fail closed (a resume refused on no evidence
# costs little), and WRONG for confirming a shutdown: "the probe broke" would
# read as "the process exited", settling the trace row while the agent keeps
# running — the very bug the confirmation exists to catch.
LIVE = "live"
GONE = "gone"
UNKNOWN = "unknown"
INDETERMINATE = "indeterminate"


def session_liveness(trace_id: str) -> str:
    """LIVE / GONE / UNKNOWN / INDETERMINATE for `trace_id`'s registered pane.

    GONE is a POSITIVE reading: the pane was queried and something other than
    the registered claude holds it.

    The two non-answers are NOT the same, and collapsing them is how a failed
    probe comes to read as a successful exit. UNKNOWN is "there was nothing to
    ask" — a clean read that found no pane row, or a bridge the operator
    switched off — which for a caller deciding whether it can end a session is
    a settled no. INDETERMINATE is "regin could not get an answer": the pane
    registry itself would not answer, or it named a pane tmux then refused to
    describe. There the honest reading is that the session may still be
    running, and no caller may report it stopped.
    """
    if not settings.agent_bridge.enabled:
        return UNKNOWN
    row, answered = store.resolve_reachable_pane(trace_id)
    if not answered:
        return INDETERMINATE
    if row is None:
        return UNKNOWN
    identity = _verify_identity(row)
    if identity["ok"]:
        return LIVE
    return GONE if identity.get("read") else INDETERMINATE


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


def capture_screen(trace_id: str, lines: int | None = None,
                   color: bool = True) -> CaptureResult:
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

    `color=False` drops `-e`, which is what a PARSER wants: SGR sequences
    split the very tokens `screen_state` anchors on.
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
    args = ["capture-pane", "-t", pane, "-p"] + (["-e"] if color else [])
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


def live_state(trace_id: str, accept: set | None = None):
    """Parsed live status of `trace_id`'s pane, or None for no verdict.

    The rich per-session read behind the /live card's NOW zone. None covers
    every "we cannot tell" case — bridge off, no reachable pane, a failed
    identity check, an unparseable screen — and callers must fall back to the
    span-derived phase rather than treating it as idle.

    The screen's own footer id is checked against `accept` (the session's
    alias group; defaults to `trace_id` alone) on top of `capture_screen`'s
    pid guards. A registry row names the pane, never the session inside it,
    so a terminal reused across sessions can pass the pid check while showing
    a different conversation — and reporting THAT one's status under this id
    is precisely the lie this module exists to remove.
    """
    from lib.agent_bridge import screen_state
    result = capture_screen(trace_id, color=False)
    if not result.ok:
        return None
    state = screen_state.parse_screen(result.text)
    if state is None:
        return None
    ids = accept or {trace_id}
    if state.session_id and state.session_id not in ids:
        log.write("bridge_screen_identity_mismatch", trace_id=trace_id,
                  on_screen=state.session_id)
        return None
    return state


# Ceiling on how many panes one sweep will capture. Pane count is bounded by
# nothing on the host side — a machine with several tmux servers can hold far
# more claude panes than any session list shows — and each capture is a
# subprocess, so the sweep needs its own bound rather than the caller's.
_MAX_SCREEN_PANES = 32


def _list_claude_panes(socket: str | None) -> list[str]:
    """Pane ids on `socket` whose foreground process is an allowlisted claude.

    One tmux call for the whole server — the batch read exists so a session
    list costs a bounded number of subprocesses instead of two per row.
    """
    r = _tmux(socket, "list-panes", "-a", "-F",
              "#{pane_id}\t#{pane_current_command}")
    if r.returncode != 0:
        return []
    allowed = settings.agent_bridge.allowed_pane_commands
    panes = []
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1] in allowed:
            panes.append(parts[0])
    return panes


def screen_states() -> dict:
    """`{session_uuid: ScreenState}` for every live claude pane tmux can see.

    Keyed by the UUID claude PRINTS in its footer, not by
    `bridge_panes.trace_id`. That registry is append-only and never retired,
    so one long-lived terminal accumulates a row per session that ever ran in
    it and cannot say which is in there now; the footer can, because the TUI
    renders its own session id. Sockets come from the registry (that is how
    regin knows which tmux servers are its own), but pane discovery does not,
    which is what lets this see a session whose row is stale.
    """
    if not settings.agent_bridge.enabled:
        return {}
    from lib.agent_bridge import screen_state
    out: dict = {}
    budget = _MAX_SCREEN_PANES
    for socket in store.list_tmux_sockets():
        for pane in _list_claude_panes(socket)[:budget]:
            budget -= 1
            r = _tmux(socket, "capture-pane", "-t", pane, "-p")
            if r.returncode != 0:
                continue
            state = screen_state.parse_screen(r.stdout)
            if state and state.session_id:
                out[state.session_id] = state
    return out
