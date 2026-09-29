"""Answering an interactive ask by driving its select TUI in the pane:
single and multi-question AskUserQuestion answers, decision options, and
live-menu picks.
"""

from __future__ import annotations

import time

from lib.agent_bridge import store
from lib.agent_bridge.menu_parse import parse_multi_select, parse_select_menu
from lib.agent_bridge.pane_io import (
    DeliveryResult,
    _await_pane_text,
    _composer_ack_refusal,
    _pane_shows_question,
    _paste_chip_high_water,
    _pasted_payload,
    _rate_ok,
    _refuse,
    _tmux,
    _verify_identity,
    log,
    sanitize_text,
)
from lib.settings import settings


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
    return _send_free_text_answer(socket, pane, free_text, is_chat)


def _open_chat_composer(socket: str | None, pane: str) -> int | None:
    """Dismiss the menu into the composer; return the pre-type paste-chip
    baseline (None when the pane read failed)."""
    # Chat: this Enter dismisses the menu into the composer before typing.
    _tmux(socket, "send-keys", "-t", pane, "Enter")
    time.sleep(0.1)
    # A long chat body collapses to a `[Pasted text #N]` chip that echoes
    # neither head nor tail, so only a chip numbered above this pre-type
    # baseline can prove the send landed. A read that FAILED is unknown,
    # not zero — see `_type_and_ack`.
    read = _tmux(socket, "capture-pane", "-pt", pane, "-S", "-40")
    return _paste_chip_high_water(read.stdout) if read.returncode == 0 else None


def _send_free_text_answer(socket: str | None, pane: str, free_text: str,
                           is_chat: bool) -> DeliveryResult:
    baseline = _open_chat_composer(socket, pane) if is_chat else None
    # Chat lands in the REAL composer, so it is pasted and acked exactly as a
    # steer is. The non-chat branch types into the ask's own inline field — a
    # different widget, unprobed, which neither scrolls nor collapses and which
    # `_reset_composer` does not address: left verbatim, strict ack, no clear.
    r = _tmux(socket, "send-keys", "-l", "-t", pane, "--",
              _pasted_payload(free_text) if is_chat else free_text)
    if r.returncode != 0:
        return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
    if is_chat:
        refusal = _composer_ack_refusal(socket, pane, free_text, baseline)
    elif _await_pane_text(socket, pane, free_text[:30]):
        refusal = None
    else:
        refusal = "not visible in pane; not submitting"
    if refusal:
        return DeliveryResult(False, f"typed answer {refusal}")
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


# A MULTI-SELECT question is a different widget from a single-select one, and
# the difference is entirely in the row list (claude v2.1.252, probed live):
#
#   single: 0..N-1 options · N "Type something." · N+1 "Chat about this"
#   multi:  0..N-1 options · N "Type something" · N+1 "Next"/"Submit" · N+2 chat
#
# Enter on a single-select option selects AND advances; on a multi-select option
# it TOGGLES `[ ]`→`[✔]` and leaves the cursor where it is. So a multi-select
# question is driven as: toggle each chosen row, then Enter the N+1 row to
# advance. That row reads "Next" mid-walk and "Submit" on the last question, so
# it is targeted by index, never by label.
_MULTI_ADVANCE_OFFSET = 1


def _is_index(value) -> bool:
    """A real 0-based index. `isinstance(True, int)` is True in Python, and a
    bool reaching the arrow arithmetic is unvalidated input driving keystrokes."""
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_multi_answer(a) -> tuple | None:
    """A multi-select entry → (sorted_indices, option_count), or None if the
    shape is invalid. `option_count` is what locates the advance row, so it is
    required and must cover every index."""
    indices, count = a.get("option_index"), a.get("option_count")
    if not _is_index(count) or count < 1 or count > _ANSWER_MAX_NAV:
        return None
    if not indices or len(indices) > count:
        return None
    if not all(_is_index(i) and 0 <= i < count for i in indices):
        return None
    # A free-typed answer rides the "Type something." row, which is a
    # different verb from toggling — refuse the mix rather than guess.
    if a.get("text") is not None:
        return None
    return (sorted(set(indices)), count)


def _parse_pick_answer(a) -> tuple | None:
    """A single-select entry → (option_index, clean_text|None), or None."""
    oi = a.get("option_index")
    if not isinstance(oi, int) or oi < 0 or oi > _ANSWER_MAX_NAV:
        return None
    raw = a.get("text")
    if raw is None:
        return (oi, None)
    text = sanitize_text(raw) if isinstance(raw, str) else ""
    return (oi, text) if text else None


def _parse_one_answer(a) -> tuple | None:
    """One answer dict → a `(option_index, clean_text|None, confirm_text|None)`
    pick, or a `(indices, option_count, confirm_text|None)` multi-select toggle
    (distinguished by a LIST `option_index`). None if invalid."""
    if not isinstance(a, dict):
        return None
    confirm = a.get("confirm_text")
    confirm = confirm if isinstance(confirm, str) else None
    parse = (_parse_multi_answer if isinstance(a.get("option_index"), list)
             else _parse_pick_answer)
    parsed = parse(a)
    return None if parsed is None else (parsed[0], parsed[1], confirm)


def _send_toggles(socket, pane, widget, desired: set) -> DeliveryResult:
    """Enter each row whose state has to flip, navigating from the MEASURED
    cursor. The symmetric difference is what makes this idempotent: a row that
    already holds its wanted state is left alone rather than toggled off."""
    cursor = widget.cursor_index
    live = {i for i, on in enumerate(widget.checked) if on}
    for target in sorted(desired ^ live):
        _navigate_delta(socket, pane, target - cursor)
        cursor = target
        r = _tmux(socket, "send-keys", "-t", pane, "Enter")
        if r.returncode != 0:
            return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
        time.sleep(_NAV_STEP_SEC)
    return DeliveryResult(True, "toggles sent")


def _toggle_multi(row: dict, indices: list, option_count: int) -> DeliveryResult:
    """Bring the focused multi-select question to exactly `indices`, then Enter
    its advance row.

    Driven from the PARSED widget, never from an assumed one. Three things are
    measured rather than trusted, because each was a way to submit a subset the
    operator never chose:

      * the widget itself — `parse_multi_select` returns None for a
        single-select menu, where these same Enters would SELECT and advance;
      * `option_count` — the advance row's position. A stale card naming a
        different count would drive Enter at an arbitrary row;
      * which boxes are already ticked. Toggling the requested set blindly
        UNTICKS a box a human (or a failed earlier attempt) already ticked, so
        the toggles sent are the symmetric difference against the live state.
        That also makes a retry after a partial failure converge instead of
        undoing itself.

    The post-condition is checked per row inside the widget, not as a count:
    a count cannot tell WHICH rows are ticked, and one stale `[✔]` elsewhere on
    screen would let a dropped toggle pass. On any mismatch this refuses
    without touching the advance row, leaving the ask parked and re-answerable.
    """
    socket, pane = row.get("tmux_socket"), row["pane_id"]
    widget = parse_multi_select(_capture_recent(socket, pane, 40))
    if widget is None:
        return DeliveryResult(False, "focused question is not multi-select")
    if widget.option_count != option_count:
        return DeliveryResult(
            False, f"pane shows {widget.option_count} options, not {option_count}")
    desired = set(indices)
    step = _send_toggles(socket, pane, widget, desired)
    if not step.delivered:
        return step
    after = parse_multi_select(_capture_recent(socket, pane, 40))
    if after is None:
        return DeliveryResult(False, "lost the question after toggling")
    ticked = {i for i, on in enumerate(after.checked) if on}
    if ticked != desired:
        return DeliveryResult(
            False, f"pane has {sorted(ticked)} ticked, wanted {sorted(desired)}; "
                   "not advancing")
    _navigate_delta(socket, pane,
                    option_count + _MULTI_ADVANCE_OFFSET - after.cursor_index)
    r = _tmux(socket, "send-keys", "-t", pane, "Enter")
    if r.returncode != 0:
        return DeliveryResult(False, f"send-keys failed: {r.stderr.strip()}")
    return DeliveryResult(True, f"toggled {len(desired)} options")


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
    for i, (first, second, confirm) in enumerate(parsed):
        if confirm and not _pane_shows_question(socket, pane, confirm):
            return DeliveryResult(False, f"question {i + 1} not focused in pane")
        step = (_toggle_multi(row, first, second) if isinstance(first, list)
                else _answer_one(row, first, second))
        if not step.delivered:
            return DeliveryResult(False, f"question {i + 1}: {step.detail}")
    return DeliveryResult(True, "all questions answered")


def deliver_answers(trace_id: str, answers) -> DeliveryResult:
    """Answer a MULTI-question AskUserQuestion by walking its tabbed select TUI.

    `answers` is the ordered per-question list ({option_index, text?,
    confirm_text?}). Each question is answered in order (focus-guarded); the
    select Enter checks that tab and advances. A question whose `option_index`
    is a LIST is multi-select: its chosen rows are toggled and its own advance
    row Enter'd instead (`_toggle_multi`), which needs `option_count` to locate
    that row. After the last, the review/Submit tab is verified and Enter'd to
    submit — so a SINGLE multi-select question is sent through here too, since
    that final Submit is mandatory for it as well. Refuses (structured, never raising) on
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
