"""Tests locking in support for sessionTitle / retry / updatedMCPToolOutput.

These were spec fields the first-pass refactor missed. Caught during the
iteration-3 re-audit against the official docs.
"""

import json

from hook_manager.core import HookResponse
from hook_manager.merge import merge_responses, response_to_json


# ── sessionTitle (UserPromptSubmit only) ──────────────────────────────

def test_session_title_emitted_on_user_prompt_submit():
    merged = merge_responses([HookResponse(session_title='Fixing the parser')])
    j = response_to_json('UserPromptSubmit', merged)
    assert j['hookSpecificOutput']['sessionTitle'] == 'Fixing the parser'


def test_session_title_suppressed_on_other_events():
    # A handler that misfires on the wrong event should not leak the field.
    merged = merge_responses([HookResponse(session_title='X')])
    for ev in ('PreToolUse', 'Stop', 'SessionStart'):
        j = response_to_json(ev, merged)
        assert 'sessionTitle' not in j.get('hookSpecificOutput', {})


def test_session_title_last_writer_wins():
    merged = merge_responses([
        HookResponse(session_title='First'),
        HookResponse(session_title='Second'),
    ])
    assert merged.session_title == 'Second'


# ── retry (PermissionDenied only) ─────────────────────────────────────

def test_retry_emitted_on_permission_denied():
    merged = merge_responses([HookResponse(retry=True)])
    j = response_to_json('PermissionDenied', merged)
    assert j['hookSpecificOutput']['retry'] is True


def test_retry_false_explicit():
    merged = merge_responses([HookResponse(retry=False)])
    j = response_to_json('PermissionDenied', merged)
    assert j['hookSpecificOutput']['retry'] is False


def test_retry_suppressed_on_other_events():
    merged = merge_responses([HookResponse(retry=True)])
    j = response_to_json('PreToolUse', merged)
    assert 'retry' not in j.get('hookSpecificOutput', {})


# ── updatedMCPToolOutput (PostToolUse MCP-only) ───────────────────────

def test_updated_mcp_tool_output_emitted_on_post_tool_use():
    payload = {'rewritten': True, 'value': 42}
    merged = merge_responses([HookResponse(updated_mcp_tool_output=payload)])
    j = response_to_json('PostToolUse', merged)
    assert j['hookSpecificOutput']['updatedMCPToolOutput'] == payload


def test_updated_mcp_tool_output_suppressed_elsewhere():
    merged = merge_responses([HookResponse(updated_mcp_tool_output={'x': 1})])
    j = response_to_json('PreToolUse', merged)
    assert 'updatedMCPToolOutput' not in j.get('hookSpecificOutput', {})


def test_updated_mcp_tool_output_accepts_non_dict():
    # Spec says "any" — list, string, number, etc. all legal.
    merged = merge_responses([HookResponse(updated_mcp_tool_output=[1, 2, 3])])
    j = response_to_json('PostToolUse', merged)
    assert j['hookSpecificOutput']['updatedMCPToolOutput'] == [1, 2, 3]


def test_updated_mcp_tool_output_last_writer_wins_with_stderr_warning(capsys):
    merge_responses([
        HookResponse(updated_mcp_tool_output={'a': 1}),
        HookResponse(updated_mcp_tool_output={'b': 2}),
    ])
    captured = capsys.readouterr()
    assert 'updated_mcp_tool_output' in captured.err


# ── End-to-end JSON round-trip ────────────────────────────────────────

def test_full_user_prompt_submit_response_round_trip():
    merged = merge_responses([
        HookResponse(
            additional_context='note',
            session_title='Refactor hooks',
        ),
    ])
    j = response_to_json('UserPromptSubmit', merged)
    # Assert the JSON is well-formed and round-trips
    assert json.dumps(j) and json.loads(json.dumps(j)) == j
    assert j['hookSpecificOutput']['hookEventName'] == 'UserPromptSubmit'
    assert j['hookSpecificOutput']['sessionTitle'] == 'Refactor hooks'
    assert j['hookSpecificOutput']['additionalContext'] == 'note'
