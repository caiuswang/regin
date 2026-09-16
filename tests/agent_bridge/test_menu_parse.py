"""`lib/agent_bridge/menu_parse.py` — the live select-menu text parser.

Every case is grounded in what a real pane actually shows: the plan-menu
fixture below is the VERBATIM `tmux capture-pane` output read from a live
Claude Code v2.1.221 session (regin trace 197d3fea, span 7b47961b) while its
`permission.request` span carried only `option_count=1, default_option_id
="deny"` — the gap this parser exists to close. The refusal cases pin the
"never guess" contract: any ambiguity must return `None`, not a best-effort
answer.
"""

from __future__ import annotations

from lib.agent_bridge.menu_parse import parse_multi_select, parse_select_menu

PLAN_MENU_CAPTURE = """\
──────────────────────────────────────────────────────────────────────────
 Claude has written up a plan and is ready to execute. Would you like to proceed?

 ❯ 1. Yes, auto-accept edits
   2. Yes, manually approve edits
   3. No, refine with Ultraplan on Claude Code on the web
   4. Tell Claude what to change
      shift+tab to approve with this feedback

 ctrl+g to edit in Nvim · ~/.claude/plans/mutable-conjuring-dijkstra.md
"""


def test_parses_the_real_plan_menu_capture():
    menu = parse_select_menu(PLAN_MENU_CAPTURE)

    assert menu is not None
    assert menu.options == [
        "Yes, auto-accept edits",
        "Yes, manually approve edits",
        "No, refine with Ultraplan on Claude Code on the web",
        "Tell Claude what to change",
    ]
    assert menu.cursor_index == 0


def test_cursor_on_a_later_option_is_read_correctly():
    text = """
   1. Yes
 ❯ 2. Yes, and don't ask again
   3. No
"""
    menu = parse_select_menu(text)

    assert menu is not None
    assert menu.cursor_index == 1
    assert menu.options[1] == "Yes, and don't ask again"


def test_picks_the_last_contiguous_run_over_earlier_plan_body_lists():
    text = """
 Here is Claude's plan:
 1. do the thing
 2. do the other thing

 Would you like to proceed?
 ❯ 1. Yes
   2. No
"""
    menu = parse_select_menu(text)

    assert menu is not None
    assert menu.options == ["Yes", "No"]


def test_no_cursor_refuses_a_stale_capture():
    text = """
   1. Yes
   2. No
"""
    assert parse_select_menu(text) is None


def test_two_cursors_refuses_an_ambiguous_capture():
    text = """
 ❯ 1. Yes
 ❯ 2. No
"""
    assert parse_select_menu(text) is None


def test_gap_in_numbering_refuses():
    text = """
 ❯ 1. Yes
   3. No
"""
    assert parse_select_menu(text) is None


def test_single_option_refuses_below_the_minimum():
    text = "❯ 1. Deny\n"
    assert parse_select_menu(text) is None


def test_empty_text_refuses():
    assert parse_select_menu("") is None
    assert parse_select_menu(None) is None


def test_hint_lines_are_not_mistaken_for_options():
    text = """
 ❯ 1. Yes, auto-accept edits
   2. Yes, manually approve edits
      shift+tab to approve with this feedback
"""
    menu = parse_select_menu(text)

    assert menu is not None
    assert len(menu.options) == 2


# ── multi-select widgets ─────────────────────────────────────────────
# A multi-select question renders its options as checkboxes and adds an
# unnumbered "Next"/"Submit" row the ordinals skip — so ordinals and arrow
# positions diverge past it, which is why the cursor is only trusted inside
# the boxed run.

def _widget(labels, *, checked=(), cursor=0, above=""):
    lines = ([above] if above else []) + ["pick many?"]
    for i, label in enumerate(labels):
        mark = "✔" if i in checked else " "
        lines.append(f"{'❯ ' if i == cursor else '  '}{i + 1}. [{mark}] {label}")
    n = len(labels)
    lines.append(f"{'❯ ' if cursor == n else '  '}{n + 1}. [ ] Type something")
    lines.append("     Submit")
    lines.append(f"{'❯ ' if cursor == n + 1 else '  '}{n + 2}. Chat about this")
    return "\n".join(lines)


def test_multi_select_reports_state_cursor_and_a_measured_option_count():
    parsed = parse_multi_select(
        _widget(("A", "B", "C"), checked={0, 2}, cursor=1))

    assert parsed.checked == [True, False, True]
    assert parsed.cursor_index == 1
    # Measured off the screen — the boxed run minus its free-text row.
    assert parsed.option_count == 3


def test_a_single_select_menu_is_not_a_multi_select_widget():
    """The whole point of the guard: on a single-select menu the toggle
    keystrokes would SELECT and advance instead."""
    single = ("pick one?\n❯ 1. Yes\n  2. No\n  3. Type something.\n"
              "  4. Chat about this")

    assert parse_multi_select(single) is None


def test_a_tick_above_the_widget_does_not_join_it():
    """Scrollback from an earlier answered ask is not part of the live
    question — counting `[✔]` across the capture is what this rules out."""
    parsed = parse_multi_select(
        _widget(("A", "B"), above="  earlier ask: 1. [✔] Alpha"))

    assert parsed.checked == [False, False]
    assert parsed.option_count == 2


def test_a_cursor_past_the_boxed_run_refuses():
    """On "Chat about this" the ordinal is N+2 but the arrow position is one
    further still, so a delta computed from it would navigate wrong."""
    assert parse_multi_select(
        _widget(("A", "B"), cursor=3)) is None


def test_a_widget_with_no_free_text_row_refuses():
    """One boxed row alone cannot be told apart from a stray line, and the
    option count is read as the boxed run minus that row."""
    thin = "pick many?\n❯ 1. [ ] A\n  2. Chat about this"

    assert parse_multi_select(thin) is None
