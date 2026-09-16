"""`lib/agent_bridge/screen_state.py` — the live-status screen parser.

The two fixtures under `fixtures/` are VERBATIM `tmux capture-pane -p` output
read from live Claude Code panes (regin sessions d7509d5d and ab625359) — one
mid-turn, one at the prompt. They are the ground truth the whole feature rests
on, so the tests read them rather than a hand-written approximation.

The bar throughout: a screen the parser cannot read must yield None, never a
verdict. `_screen_phase` maps None to "keep the span-derived phase", so a
wrong `busy=False` is the one failure that silently reinstates the bug this
module exists to remove.
"""

from pathlib import Path

import pytest

from lib.agent_bridge.screen_state import (
    _STATUS_LOOKBACK, as_payload, parse_screen,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / f"screen_{name}.txt").read_text()


def test_busy_pane_yields_the_turn_state_the_terminal_shows():
    state = parse_screen(_fixture("busy"))
    assert state is not None
    assert state.busy is True
    assert state.verb == "Sock-hopping"
    assert state.elapsed_sec == 5 * 60 + 34
    assert state.tokens == 18_200
    assert state.tool.startswith("Bash(")
    assert state.session_id == "d7509d5d-b9ad-4cc3-88aa-8df7c278817d"
    assert state.ctx_pct == 11
    assert state.branch == "feat/trace-chrome-redesign"


def test_idle_pane_is_not_busy_and_still_names_its_session():
    state = parse_screen(_fixture("idle"))
    assert state is not None
    assert state.busy is False
    assert state.verb is None and state.elapsed_sec is None
    assert state.session_id == "ab625359-3893-45f1-8c15-707005996eb0"
    assert state.ctx_pct == 13


@pytest.mark.parametrize("text", [
    "",
    "just a shell prompt\n$ ls\n",
    # A `❯` that is a select-menu cursor, not the composer: no fencing rules,
    # so it must not license an idle verdict.
    "❯ 1. Yes\n  2. No\n",
])
def test_an_unreadable_screen_has_no_verdict(text):
    assert parse_screen(text) is None


def test_a_busy_line_without_the_composer_box_is_not_a_verdict():
    """Half a frame (a resize caught mid-render) is not evidence of anything."""
    assert parse_screen("✻ Thinking… (12s · ↓ 400 tokens)\n") is None


def test_busy_is_read_off_the_elapsed_clock_not_the_interrupt_hint():
    """claude 2.x drops `esc to interrupt` on long turns — a needle on that
    string reads a working session as idle, which is the exact regression the
    phase override exists to fix."""
    rule = "─" * 40
    frame = "\n".join([
        "⏺ Bash(sleep 600)",
        "  ⎿  Running…",
        "",
        "✳ Ruminating… (2h 5m 3s · ↓ 1.2m tokens)",
        rule,
        "❯ ",
        rule,
        "  d7509d5d-b9ad-4cc3-88aa-8df7c278817d  \\~/regin (main) Opus 5 ctx:9%;",
    ])
    assert "esc to interrupt" not in frame
    state = parse_screen(frame)
    assert state.busy is True
    assert state.elapsed_sec == 2 * 3600 + 5 * 60 + 3
    assert state.tokens == 1_200_000
    assert state.tool == "Bash(sleep 600)"


def test_a_returned_tool_is_history_not_the_in_flight_call():
    rule = "─" * 40
    frame = "\n".join([
        "⏺ Bash(git status)",
        "  ⎿  On branch main",
        "",
        "✳ Thinking… (9s · ↓ 100 tokens)",
        rule,
        "❯ ",
        rule,
    ])
    state = parse_screen(frame)
    assert state.busy is True
    assert state.tool is None


def test_an_unsent_draft_rides_the_idle_frame():
    rule = "─" * 40
    frame = "\n".join([rule, "❯ fix the marker split", rule])
    state = parse_screen(frame)
    assert state.busy is False
    assert state.draft == "fix the marker split"


def test_as_payload_drops_nothing_the_card_renders_and_passes_none_through():
    assert as_payload(None) is None
    payload = as_payload(parse_screen(_fixture("busy")))
    assert payload["busy"] is True
    assert payload["verb"] == "Sock-hopping"
    assert payload["elapsed_sec"] == 334
    assert payload["session_id"] == "d7509d5d-b9ad-4cc3-88aa-8df7c278817d"


# ── regressions from the adversarial verify pass ────────────────────
# Each case below produced a WRONG verdict at first cut: a `busy=False` that
# `_screen_phase` would have promoted to an authoritative idle.

def _framed_lines(*status: str) -> str:
    """A composer-fenced frame whose status region carries `status` verbatim."""
    rule = "─" * 40
    return "\n".join(["⏺ Bash(x)", "  ⎿  Running…", *status, rule, "❯ ", rule])


def _framed(status: str, gap: int = 0) -> str:
    rule = "─" * 40
    chrome = ["  ⎿  Tip: ask /btw a side question"] * gap
    return "\n".join(["⏺ Bash(x)", "  ⎿  Running…", *chrome,
                      status, rule, "❯ ", rule])


@pytest.mark.parametrize("status,elapsed", [
    # The field order inside the parenthetical differs across builds; the
    # clock is not reliably first.
    ("✻ Simmering… (5m 34s · ↓ 18.2k tokens)", 334),
    ("✻ Simmering… (↓ 1.2k tokens · 12s)", 12),
    ("✻ Simmering… (esc to interrupt · 1m 4s)", 64),
    # A build that renders neither spinner-gerund nor a clock is still busy.
    ("· (esc to interrupt)", None),
])
def test_a_status_line_is_busy_whichever_field_the_build_renders(status, elapsed):
    state = parse_screen(_framed(status))
    assert state.busy is True
    assert state.elapsed_sec == elapsed


def test_hint_chrome_between_status_and_composer_does_not_hide_the_turn():
    state = parse_screen(_framed("✻ Simmering… (12s)", gap=5))
    assert state.busy is True


def test_a_select_menu_fenced_by_plain_rules_is_not_a_composer():
    """It reads as an idle prompt AND leaks the focused option as a draft."""
    rule = "─" * 40
    menu = "\n".join([rule, "❯ 2. Yes, and don't ask again", "  3. No", rule])
    assert parse_screen(menu) is None


# ── The retry banner ────────────────────────────────────────────────
# `<label> · Retrying in <dur>[ (<resets>)] · attempt <n>/<max>`, drawn in the
# status region beside — not instead of — the spinner line. A retrying turn is
# still working, so `busy` must survive it: reading it as idle would put the
# operator back where this whole feature started.

_SPINNER = "✻ Working… (6m 18s · ↑ 1.2k tokens · esc to interrupt)"


def test_a_retry_banner_rides_the_busy_frame_without_costing_the_spinner():
    state = parse_screen(_framed_lines(
        "Connection error · Retrying in 30s · attempt 5/10", _SPINNER))
    assert state.busy is True
    assert state.retry_label == "Connection error"
    assert state.retry_in == "30s"
    assert state.retry_attempt == 5
    assert state.retry_max == 10
    assert state.verb == "Working"
    assert state.elapsed_sec == 6 * 60 + 18
    assert state.tokens == 1_200


def test_a_busy_frame_with_no_retry_carries_no_retry_fields():
    state = parse_screen(_framed_lines(_SPINNER))
    assert state.busy is True
    assert state.verb == "Working"
    assert state.retry_label is None
    assert state.retry_in is None
    assert state.retry_attempt is None
    assert state.retry_max is None


@pytest.mark.parametrize("line,label,wait", [
    ("Usage limit reached (2pm) · Retrying in 1m 4s · attempt 3/10",
     "Usage limit reached (2pm)", "1m 4s"),
    # The `(resets …)` note sits inside the duration segment on some builds.
    # It is a wall clock, so it is tolerated and dropped rather than captured.
    ("Usage limit reached · Retrying in 30s (resets 2pm) · attempt 3/10",
     "Usage limit reached", "30s"),
    ("API error · Retrying in 8s · attempt 3/10", "API error", "8s"),
])
def test_the_rate_limit_variant_keeps_its_whole_label(line, label, wait):
    state = parse_screen(_framed_lines(line, _SPINNER))
    assert state.retry_label == label
    assert state.retry_in == wait
    assert state.retry_attempt == 3
    assert state.retry_max == 10


def test_a_retry_line_in_scrollback_is_a_resolved_turn_not_this_one():
    """Outside `_STATUS_LOOKBACK` it belongs to a turn that already finished."""
    rule = "─" * 40
    scrollback = ["Connection error · Retrying in 30s · attempt 5/10"]
    scrollback += ["  ⎿  Tip: ask /btw a side question"] * (_STATUS_LOOKBACK + 4)
    frame = "\n".join([*scrollback, _SPINNER, rule, "❯ ", rule])
    state = parse_screen(frame)
    assert state.busy is True
    assert state.retry_attempt is None
    assert state.retry_max is None
    assert state.retry_label is None


# The three fixtures below are the VERBATIM `screen_busy.txt` capture with the
# banner block spliced in beside its spinner — derived from a real frame, not
# captured from one. The banner text and its layout (own line, width-2 arrow
# gutter, the `noResponse` hint line under it) come from the render tree of the
# claude binary; the surrounding chrome, composer fencing and footer are the
# real pane's. So they check `parse_screen` against claude's rendered SHAPE
# rather than against the regex's own assumptions — but they are not proof
# against a live retrying pane, which nothing here has captured.

def test_the_rate_limit_banner_parses_out_of_a_real_frame():
    state = parse_screen(_fixture("retry_rate_limit"))
    assert state.busy is True
    # The turn's own spinner still answers for verb/elapsed/tokens/session.
    assert state.verb == "Sock-hopping"
    assert state.elapsed_sec == 5 * 60 + 34
    assert state.tokens == 18_200
    assert state.session_id == "d7509d5d-b9ad-4cc3-88aa-8df7c278817d"
    # The `↓` gutter glyph is chrome, not part of the label.
    assert state.retry_label == "Usage limit reached (2pm)"
    assert state.retry_in == "1m 4s"
    assert state.retry_attempt == 3
    assert state.retry_max == 10
    assert state.retry_final is None


def test_the_no_response_banner_is_a_second_variant_not_a_spelling():
    """`error.noResponse` says `retrying, waiting up to <dur>` in lowercase —
    a long silent stall renders this, not the rate-limit sentence."""
    state = parse_screen(_fixture("retry_no_response"))
    assert state.busy is True
    assert state.retry_label == "No response from the API after 8m 12s"
    assert state.retry_in == "4m"
    assert state.retry_attempt == 3
    assert state.retry_max == 10
    assert state.retry_final is None


def test_the_final_retry_names_no_attempt_and_none_is_invented():
    """`retrying once` carries no counter. The screen did not say which
    attempt this is, so neither does `retry_attempt`."""
    state = parse_screen(_fixture("retry_final"))
    assert state.busy is True
    assert state.retry_label == "No response from the API after 16m 30s"
    assert state.retry_in == "8m"
    assert state.retry_attempt is None
    assert state.retry_max is None
    assert state.retry_final is True


def test_the_previous_turns_banner_does_not_attach_to_this_turns_spinner():
    """A settled turn's banner can still be drawn inside `_STATUS_LOOKBACK`
    when the NEXT turn starts. Read across that boundary it put `attempt 5/10`
    over a healthy turn and persisted an ERROR span under a prompt that never
    retried — so the banner binds to the SPINNER, not to the window."""
    rule = "─" * 40
    frame = "\n".join([
        "  ↓ Connection error · Retrying in 30s · attempt 5/10",
        "",
        "⏺ Bash(x)",
        "  ⎿  Running…",
        "",
        _SPINNER,
        rule, "❯ ", rule,
    ])
    state = parse_screen(frame)
    assert state.busy is True
    assert state.verb == "Working"
    assert state.retry_attempt is None
    assert state.retry_label is None
    assert state.retry_final is None


def test_a_banner_with_no_spinner_is_a_settled_turn_not_a_live_retry():
    """The last banner of a turn stays on screen after the turn settles, so on
    its own it is history. Deriving `busy` from it read a session sitting at
    the prompt as working AND persisted a span for a retry already over — the
    mirror image of the wrong-idle asymmetry `_screen_phase` refuses to make."""
    rule = "─" * 40
    frame = "\n".join([
        "Connection error · Retrying in 30s · attempt 5/10",
        *["  ⎿  Tip: ask /btw a side question"] * 9,
        rule, "❯ ", rule,
    ])
    state = parse_screen(frame)
    assert state.busy is False
    assert state.retry_attempt is None
    assert state.retry_max is None
    assert state.retry_label is None
    assert state.retry_in is None


def test_as_payload_carries_the_retry_the_card_renders():
    payload = as_payload(parse_screen(_framed_lines(
        "API error · Retrying in 8s · attempt 2/10", _SPINNER)))
    assert payload["busy"] is True
    assert payload["retry_label"] == "API error"
    assert payload["retry_in"] == "8s"
    assert payload["retry_attempt"] == 2
    assert payload["retry_max"] == 10
    assert payload["retry_final"] is None
