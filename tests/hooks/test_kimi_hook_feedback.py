"""Kimi write-back path: handler feedback must survive the Kimi serializer.

Grounded in Kimi Code 0.29.1 (`dist/main.mjs`): hook stdout is piped and read
back verbatim on the prompt event, and a blockable event surfaces only the
exit-2 stderr reason. Claude serialization must stay byte-identical.
"""

import json

from hook_manager.core import HookResponse
from hook_manager.merge import (
    kimi_block_reason,
    kimi_response_text,
    merge_responses,
    response_to_json,
)


def test_non_prompt_events_emit_nothing_for_kimi():
    """Kimi 0.29.1 reads hook stdout back only on the prompt path; every other
    event's stdout is piped and discarded. Emitting there would be dead bytes,
    so PostToolUse feedback is deferred to the queue instead (see
    test_kimi_feedback_queue.py)."""
    merged = merge_responses([
        HookResponse(suppress_output=True,
                     additional_context='rule violation: bare except at foo.py:12'),
    ])
    for event in ('PostToolUse', 'PostToolUseFailure', 'Stop', 'SubagentStop'):
        assert kimi_response_text(event, merged) == ''


def test_prompt_context_is_plain_text_not_json():
    """Kimi renders stdout verbatim, so a JSON blob would pollute the model
    context. Anything we emit for context must not parse as a JSON object."""
    merged = merge_responses([HookResponse(additional_context='doc_check: 2 findings')])
    for event in ('UserPromptSubmit', 'SessionStart'):
        text = kimi_response_text(event, merged)
        assert text == 'doc_check: 2 findings'
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        assert not isinstance(parsed, dict)


def test_kimi_stays_silent_without_context():
    """Trace-only handlers must still produce zero stdout on Kimi."""
    merged = merge_responses([HookResponse(suppress_output=True)])
    for event in ('PostToolUse', 'PostToolUseFailure', 'Stop', 'UserPromptSubmit'):
        assert kimi_response_text(event, merged) == ''


def test_permission_decision_still_round_trips():
    merged = merge_responses([
        HookResponse(permission_decision='deny', permission_reason='use rg instead'),
    ])
    out = json.loads(kimi_response_text('PreToolUse', merged))
    assert out == {'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': 'use rg instead',
    }}


def test_permission_decision_wins_over_context_on_pretooluse():
    """PreToolUse stdout is parsed as JSON by Kimi; the permission object must
    not be displaced by, or concatenated with, context text."""
    merged = merge_responses([
        HookResponse(permission_decision='deny', permission_reason='blocked'),
        HookResponse(additional_context='fyi'),
    ])
    out = json.loads(kimi_response_text('PreToolUse', merged))
    assert out['hookSpecificOutput']['permissionDecision'] == 'deny'


def test_block_reason_carries_additional_context():
    """A blockable event shows only the exit-2 stderr reason, so the handler
    body has to ride along with it."""
    merged = merge_responses([
        HookResponse(decision='block', decision_reason='blocked by rule',
                     additional_context='fix foo.py:12'),
    ])
    assert kimi_block_reason(merged) == 'blocked by rule\n\nfix foo.py:12'


def test_block_reason_falls_back_to_context_alone():
    assert kimi_block_reason(HookResponse(additional_context='fix foo.py:12')) == 'fix foo.py:12'
    assert kimi_block_reason(HookResponse(decision_reason='blocked')) == 'blocked'
    assert kimi_block_reason(HookResponse(stop_reason='stopped')) == 'stopped'
    assert kimi_block_reason(HookResponse()) == ''


def test_claude_serialization_unchanged():
    """Claude path must be byte-identical: additionalContext still travels in
    hookSpecificOutput on every event, and nothing new appears."""
    merged = merge_responses([
        HookResponse(suppress_output=True, additional_context='rule violation: x'),
    ])
    assert response_to_json('PostToolUse', merged) == {
        'hookSpecificOutput': {
            'hookEventName': 'PostToolUse',
            'additionalContext': 'rule violation: x',
        },
    }
    assert response_to_json('UserPromptSubmit', merged) == {
        'suppressOutput': True,
        'hookSpecificOutput': {
            'hookEventName': 'UserPromptSubmit',
            'additionalContext': 'rule violation: x',
        },
    }


def test_claude_block_serialization_unchanged():
    merged = merge_responses([
        HookResponse(decision='block', decision_reason='blocked by rule',
                     additional_context='fix foo.py:12'),
    ])
    assert response_to_json('Stop', merged) == {
        'suppressOutput': True,
        'decision': 'block',
        'reason': 'blocked by rule',
        'hookSpecificOutput': {
            'hookEventName': 'Stop',
            'additionalContext': 'fix foo.py:12',
        },
    }
