"""Tests for hook_manager.merge.merge_responses and response_to_json."""

from hook_manager.core import HookResponse
from hook_manager.merge import merge_responses, response_to_json


# ── permission_decision precedence ────────────────────────────────────

def test_deny_beats_everything():
    merged = merge_responses([
        HookResponse(permission_decision='allow'),
        HookResponse(permission_decision='ask'),
        HookResponse(permission_decision='defer'),
        HookResponse(permission_decision='deny', permission_reason='nope'),
    ])
    assert merged.permission_decision == 'deny'
    assert merged.permission_reason == 'nope'


def test_defer_beats_ask_and_allow():
    merged = merge_responses([
        HookResponse(permission_decision='allow'),
        HookResponse(permission_decision='ask'),
        HookResponse(permission_decision='defer', permission_reason='waiting'),
    ])
    assert merged.permission_decision == 'defer'
    assert merged.permission_reason == 'waiting'


def test_ask_beats_allow():
    merged = merge_responses([
        HookResponse(permission_decision='allow'),
        HookResponse(permission_decision='ask', permission_reason='prompt user'),
    ])
    assert merged.permission_decision == 'ask'


def test_allow_when_only_allow():
    merged = merge_responses([HookResponse(permission_decision='allow')])
    assert merged.permission_decision == 'allow'


def test_tie_in_permission_reasons_are_joined():
    merged = merge_responses([
        HookResponse(permission_decision='deny', permission_reason='reason A'),
        HookResponse(permission_decision='deny', permission_reason='reason B'),
    ])
    assert 'reason A' in merged.permission_reason
    assert 'reason B' in merged.permission_reason


# ── decision=block (Stop / PostToolUse / UserPromptSubmit) ────────────

def test_any_block_wins():
    merged = merge_responses([
        HookResponse(),
        HookResponse(decision='block', decision_reason='stop'),
        HookResponse(),
    ])
    assert merged.decision == 'block'


def test_block_reasons_concat():
    merged = merge_responses([
        HookResponse(decision='block', decision_reason='one'),
        HookResponse(decision='block', decision_reason='two'),
    ])
    assert 'one' in merged.decision_reason and 'two' in merged.decision_reason


# ── continue_ / stop_reason ───────────────────────────────────────────

def test_continue_false_propagates():
    merged = merge_responses([
        HookResponse(continue_=True),
        HookResponse(continue_=False, stop_reason='quota'),
    ])
    assert merged.continue_ is False
    assert merged.stop_reason == 'quota'


def test_first_stop_reason_wins_when_tie():
    merged = merge_responses([
        HookResponse(continue_=False, stop_reason='first'),
        HookResponse(continue_=False, stop_reason='second'),
    ])
    assert merged.stop_reason == 'first'


# ── suppress_output ──────────────────────────────────────────────────

def test_suppress_output_and_merges():
    merged = merge_responses([
        HookResponse(suppress_output=True),
        HookResponse(suppress_output=False),
    ])
    assert merged.suppress_output is False


def test_suppress_output_true_if_only_trues():
    merged = merge_responses([
        HookResponse(suppress_output=True),
        HookResponse(suppress_output=True),
    ])
    assert merged.suppress_output is True


# ── additional_context ───────────────────────────────────────────────

def test_additional_context_joined_in_order():
    merged = merge_responses([
        HookResponse(additional_context='alpha'),
        HookResponse(additional_context='beta'),
    ])
    assert merged.additional_context == 'alpha\n---\nbeta'


def test_additional_context_skips_empty():
    merged = merge_responses([
        HookResponse(additional_context=None),
        HookResponse(additional_context='only'),
        HookResponse(additional_context=''),
    ])
    assert merged.additional_context == 'only'


# ── updated_input ────────────────────────────────────────────────────

def test_updated_input_last_writer_wins():
    merged = merge_responses([
        HookResponse(updated_input={'a': 1}),
        HookResponse(updated_input={'a': 2}),
    ])
    assert merged.updated_input == {'a': 2}


# ── exit_code ────────────────────────────────────────────────────────

def test_exit_code_max():
    merged = merge_responses([
        HookResponse(exit_code=0),
        HookResponse(exit_code=2),
        HookResponse(exit_code=1),
    ])
    assert merged.exit_code == 2


# ── response_to_json ─────────────────────────────────────────────────

def test_json_minimal_response():
    j = response_to_json('PreToolUse', HookResponse())
    assert j == {}


def test_json_suppress_output_emitted_on_supported_events():
    """Stop supports suppressOutput; handlers that set it should see it
    serialized."""
    j = response_to_json('Stop', HookResponse(suppress_output=True))
    assert j == {'suppressOutput': True}


def test_json_suppress_output_blocked_on_pre_tool_use():
    """PreToolUse harness rejects suppressOutput, so response_to_json
    must omit it even when handlers explicitly set it."""
    j = response_to_json('PreToolUse', HookResponse(suppress_output=True))
    assert 'suppressOutput' not in j


def test_json_suppress_output_blocked_on_post_tool_use():
    j = response_to_json('PostToolUse', HookResponse(suppress_output=True))
    assert 'suppressOutput' not in j


def test_json_suppress_output_blocked_on_permission_events():
    j_req = response_to_json('PermissionRequest', HookResponse(suppress_output=True))
    j_den = response_to_json('PermissionDenied', HookResponse(suppress_output=True))
    assert 'suppressOutput' not in j_req
    assert 'suppressOutput' not in j_den


def test_json_includes_permission_decision_and_context():
    merged = HookResponse(
        permission_decision='deny',
        permission_reason='bad',
        additional_context='see the log',
    )
    j = response_to_json('PreToolUse', merged)
    assert j['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert j['hookSpecificOutput']['permissionDecisionReason'] == 'bad'
    assert j['hookSpecificOutput']['additionalContext'] == 'see the log'
    assert j['hookSpecificOutput']['hookEventName'] == 'PreToolUse'


def test_json_block_becomes_decision_and_reason():
    merged = HookResponse(decision='block', decision_reason='nope')
    j = response_to_json('UserPromptSubmit', merged)
    assert j['decision'] == 'block'
    assert j['reason'] == 'nope'


def test_json_continue_false_emits_stop_reason():
    merged = HookResponse(continue_=False, stop_reason='out of budget')
    j = response_to_json('Stop', merged)
    assert j['continue'] is False
    assert j['stopReason'] == 'out of budget'


# ── Event-scoped fields: only emit on the right event ────────────────

def test_session_title_only_on_user_prompt_submit():
    """`session_title` is a UserPromptSubmit-only output field per spec.
    Emitting it on Stop or PreToolUse would be unspecified junk —
    Claude Code ignores or rejects it depending on version."""
    merged = HookResponse(session_title='Refactor auth layer')
    # Emitted here…
    j_good = response_to_json('UserPromptSubmit', merged)
    assert j_good['hookSpecificOutput']['sessionTitle'] == 'Refactor auth layer'
    # …but NOT here.
    j_bad = response_to_json('Stop', merged)
    assert 'sessionTitle' not in j_bad.get('hookSpecificOutput', {})


def test_retry_only_on_permission_denied():
    """`retry` tells Claude Code whether to retry the tool after the
    user denied permission. It only makes sense on PermissionDenied
    — on PreToolUse it'd be confused with permission_decision."""
    merged = HookResponse(retry=True)
    j_good = response_to_json('PermissionDenied', merged)
    assert j_good['hookSpecificOutput']['retry'] is True
    j_bad = response_to_json('PreToolUse', merged)
    assert 'retry' not in j_bad.get('hookSpecificOutput', {})


def test_updated_mcp_tool_output_only_on_post_tool_use():
    """Spec scopes `updatedMCPToolOutput` to PostToolUse. Surfacing it
    on PreToolUse would let a handler fake a tool response before the
    tool had run — genuinely confusing."""
    merged = HookResponse(updated_mcp_tool_output={'overridden': True})
    j_good = response_to_json('PostToolUse', merged)
    assert j_good['hookSpecificOutput']['updatedMCPToolOutput'] == {'overridden': True}
    j_bad = response_to_json('PreToolUse', merged)
    assert 'updatedMCPToolOutput' not in j_bad.get('hookSpecificOutput', {})


# ── PermissionRequest decision object ────────────────────────────────

def test_permission_request_decision_allow_emits_structured_output():
    from hook_manager.core import PermissionRequestDecision
    prd = PermissionRequestDecision(
        behavior='allow',
        updated_input={'command': 'ls -la'},
        updated_permissions=[{'ruleType': 'Bash', 'ruleContent': 'ls *'}],
    )
    merged = HookResponse(permission_request_decision=prd)
    j = response_to_json('PermissionRequest', merged)
    out = j['hookSpecificOutput']['decision']
    assert out['behavior'] == 'allow'
    assert out['updatedInput'] == {'command': 'ls -la'}
    assert out['updatedPermissions'] == [{'ruleType': 'Bash', 'ruleContent': 'ls *'}]


def test_permission_request_decision_deny_emits_message_and_interrupt():
    from hook_manager.core import PermissionRequestDecision
    prd = PermissionRequestDecision(
        behavior='deny',
        message='not allowed in auto mode',
        interrupt=True,
    )
    merged = HookResponse(permission_request_decision=prd)
    j = response_to_json('PermissionRequest', merged)
    out = j['hookSpecificOutput']['decision']
    assert out == {'behavior': 'deny', 'message': 'not allowed in auto mode', 'interrupt': True}


def test_permission_request_deny_beats_allow_across_handlers():
    """When two handlers both set permission_request_decision, deny
    wins over allow — the stricter handler's decision is kept even if
    another handler wrote allow later."""
    from hook_manager.core import PermissionRequestDecision
    deny = HookResponse(permission_request_decision=PermissionRequestDecision(
        behavior='deny', message='locked'))
    allow = HookResponse(permission_request_decision=PermissionRequestDecision(
        behavior='allow'))

    merged = merge_responses([deny, allow])
    assert merged.permission_request_decision.behavior == 'deny'
    assert merged.permission_request_decision.message == 'locked'

    # Order reversed — deny still wins (it was seen first).
    merged = merge_responses([allow, deny])
    assert merged.permission_request_decision.behavior == 'deny'


def test_permission_request_decision_not_emitted_on_wrong_event():
    """The structured decision object is PermissionRequest-only; on
    any other event, response_to_json must omit it."""
    from hook_manager.core import PermissionRequestDecision
    merged = HookResponse(permission_request_decision=PermissionRequestDecision(behavior='allow'))
    j = response_to_json('PreToolUse', merged)
    assert 'decision' not in j.get('hookSpecificOutput', {})


# ── Multi-writer warnings (stderr, non-fatal) ────────────────────────

def test_updated_input_multiple_writers_logs_warning(capsys):
    """When two handlers write `updated_input`, the merger keeps the
    last writer but logs a warning to stderr so operators notice a
    misconfigured priority chain."""
    merge_responses([
        HookResponse(updated_input={'a': 1}),
        HookResponse(updated_input={'a': 2}),
    ])
    stderr = capsys.readouterr().err
    assert 'updated_input' in stderr
    assert '2 handlers' in stderr


def test_updated_mcp_tool_output_multiple_writers_logs_warning(capsys):
    merge_responses([
        HookResponse(updated_mcp_tool_output={'v': 1}),
        HookResponse(updated_mcp_tool_output={'v': 2}),
    ])
    stderr = capsys.readouterr().err
    assert 'updated_mcp_tool_output' in stderr


# ── system_message concatenation ─────────────────────────────────────

def test_system_message_concatenated_with_newlines():
    """Multiple handlers can stamp system_message — they concatenate
    with a newline separator so the UI can render each on its own line."""
    merged = merge_responses([
        HookResponse(system_message='first note'),
        HookResponse(system_message='second note'),
        HookResponse(),  # None is ignored
        HookResponse(system_message='third note'),
    ])
    assert merged.system_message == 'first note\nsecond note\nthird note'


# ── additional_context: no handlers set it → stays None ──────────────

def test_additional_context_none_when_no_handler_sets_it():
    merged = merge_responses([HookResponse(), HookResponse()])
    assert merged.additional_context is None


# ── None response entries ────────────────────────────────────────────

def test_merge_skips_none_entries():
    """Handlers that don't care can return None; the merger must skip
    them without crashing."""
    merged = merge_responses([
        None,
        HookResponse(additional_context='real'),
        None,
    ])
    assert merged.additional_context == 'real'
