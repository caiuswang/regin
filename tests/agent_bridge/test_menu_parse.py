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

from lib.agent_bridge.menu_parse import parse_select_menu

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
