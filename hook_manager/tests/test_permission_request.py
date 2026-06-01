"""Tests for the rich PermissionRequest output shape.

Spec: the PermissionRequest hookSpecificOutput.decision is a nested object:
  {behavior: allow|deny, updatedInput?, updatedPermissions?, message?, interrupt?}

These tests lock in serialization + merge precedence.
"""

from hook_manager.core import HookResponse, PermissionRequestDecision
from hook_manager.core import HookPayload
from hook_manager.handlers import permission_events
from hook_manager.merge import merge_responses, response_to_json
import json
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"


# ── Serialization ────────────────────────────────────────────────────

def test_minimal_allow_emits_just_behavior():
    r = HookResponse(permission_request_decision=PermissionRequestDecision(behavior='allow'))
    j = response_to_json('PermissionRequest', merge_responses([r]))
    assert j['hookSpecificOutput']['decision'] == {'behavior': 'allow'}


def test_allow_with_updated_input():
    prd = PermissionRequestDecision(
        behavior='allow',
        updated_input={'command': 'ls -la'},
    )
    j = response_to_json('PermissionRequest', merge_responses([HookResponse(permission_request_decision=prd)]))
    d = j['hookSpecificOutput']['decision']
    assert d['behavior'] == 'allow'
    assert d['updatedInput'] == {'command': 'ls -la'}


def test_allow_with_permission_updates():
    updates = [{'type': 'addRules', 'rules': [{'toolName': 'Bash'}],
                'behavior': 'allow', 'destination': 'session'}]
    prd = PermissionRequestDecision(
        behavior='allow',
        updated_permissions=updates,
    )
    j = response_to_json('PermissionRequest', merge_responses([HookResponse(permission_request_decision=prd)]))
    assert j['hookSpecificOutput']['decision']['updatedPermissions'] == updates


def test_deny_with_message_and_interrupt():
    prd = PermissionRequestDecision(
        behavior='deny',
        message='policy violation',
        interrupt=True,
    )
    j = response_to_json('PermissionRequest', merge_responses([HookResponse(permission_request_decision=prd)]))
    d = j['hookSpecificOutput']['decision']
    assert d['behavior'] == 'deny'
    assert d['message'] == 'policy violation'
    assert d['interrupt'] is True


def test_deny_without_interrupt_omits_the_field():
    prd = PermissionRequestDecision(behavior='deny', message='nope')
    j = response_to_json('PermissionRequest', merge_responses([HookResponse(permission_request_decision=prd)]))
    d = j['hookSpecificOutput']['decision']
    assert 'interrupt' not in d


# ── Event scoping ────────────────────────────────────────────────────

def test_decision_not_emitted_on_other_events():
    prd = PermissionRequestDecision(behavior='allow')
    merged = merge_responses([HookResponse(permission_request_decision=prd)])
    # On any event other than PermissionRequest, the field is suppressed.
    for ev in ('PreToolUse', 'PostToolUse', 'Stop', 'PermissionDenied'):
        j = response_to_json(ev, merged)
        assert 'decision' not in j.get('hookSpecificOutput', {})


# ── Merge precedence ─────────────────────────────────────────────────

def test_deny_beats_allow_across_handlers():
    """Two handlers for PermissionRequest — one allow, one deny. Deny wins."""
    merged = merge_responses([
        HookResponse(permission_request_decision=PermissionRequestDecision(
            behavior='allow', updated_input={'x': 1})),
        HookResponse(permission_request_decision=PermissionRequestDecision(
            behavior='deny', message='nope')),
    ])
    assert merged.permission_request_decision is not None
    assert merged.permission_request_decision.behavior == 'deny'
    assert merged.permission_request_decision.message == 'nope'


def test_deny_preserved_even_if_allow_comes_later():
    merged = merge_responses([
        HookResponse(permission_request_decision=PermissionRequestDecision(
            behavior='deny', message='policy')),
        HookResponse(permission_request_decision=PermissionRequestDecision(
            behavior='allow')),
    ])
    assert merged.permission_request_decision.behavior == 'deny'


def test_two_allows_last_wins():
    merged = merge_responses([
        HookResponse(permission_request_decision=PermissionRequestDecision(
            behavior='allow', updated_input={'v': 1})),
        HookResponse(permission_request_decision=PermissionRequestDecision(
            behavior='allow', updated_input={'v': 2})),
    ])
    assert merged.permission_request_decision.updated_input == {'v': 2}


def test_none_when_no_handler_provides_one():
    merged = merge_responses([HookResponse(additional_context='x')])
    assert merged.permission_request_decision is None


# ── Provider-neutral request info ─────────────────────────────────────

def test_claude_permission_request_info_from_bash_fixture():
    data = json.loads((FIXTURES / "PermissionRequest-Bash.json").read_text())
    data["agent_type"] = "claude"
    payload = HookPayload.from_stdin_json("PermissionRequest", data)

    info = payload.permission_request
    assert info is not None
    assert info.tool_name == "Bash"
    assert "Run shell command" in info.requested_permission
    assert info.suggestions == data["permission_suggestions"]
    assert info.options[0].updated_permissions == [data["permission_suggestions"][0]]
    assert info.options[-1].id == "deny"


def test_claude_permission_request_info_from_read_fixture():
    data = json.loads((FIXTURES / "PermissionRequest-Read.json").read_text())
    data["agent_type"] = "claude"
    payload = HookPayload.from_stdin_json("PermissionRequest", data)

    info = payload.permission_request
    assert info is not None
    assert info.requested_permission == "Read file: /Users/user/.claude/skills/git/content.md"
    assert info.default_option_id == "allow_session_1"


def test_claude_provider_serializes_selected_permission_option():
    data = json.loads((FIXTURES / "PermissionRequest-Bash.json").read_text())
    data["agent_type"] = "claude"
    payload = HookPayload.from_stdin_json("PermissionRequest", data)

    response = payload.resolved_provider.serialize_permission_decision(
        payload.permission_request,
        payload.permission_request.default_option_id,
    )
    out = response_to_json("PermissionRequest", merge_responses([response]))
    decision = out["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "allow"
    assert decision["updatedPermissions"] == data["permission_suggestions"]


def test_codex_pretool_permission_request_asks_with_options():
    data = json.loads((FIXTURES / "PreToolUse-CodexPermission.json").read_text())
    payload = HookPayload.from_stdin_json("PreToolUse", data)

    info = payload.permission_request
    assert info is not None
    assert info.default_option_id == "allow_session"
    assert [o.id for o in info.options] == ["allow_session", "allow_project", "deny"]

    response = permission_events.handle_pre_tool_request(payload)
    out = response_to_json("PreToolUse", merge_responses([response]))
    specific = out["hookSpecificOutput"]
    assert specific["permissionDecision"] == "ask"
    assert "Permission requested:" in specific["permissionDecisionReason"]
    assert "allow_session" in specific["permissionDecisionReason"]


def test_plain_codex_pretool_without_permission_metadata_is_ignored():
    payload = HookPayload.from_stdin_json("PreToolUse", {
        "agentType": "codex",
        "hookEventName": "PreToolUse",
        "toolName": "Bash",
        "toolInput": {"command": "pwd"},
    })
    assert payload.permission_request is None
    assert permission_events.handle_pre_tool_request(payload) is None


# ── Pending span emission (live status) ──────────────────────────────

def _capture_spans(monkeypatch):
    import lib.hook_plugin as hp
    spans = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    return spans


def test_permission_request_emits_pending_span_with_deterministic_id(monkeypatch):
    from lib.trace.pending_spans import perm_pending_id
    spans = _capture_spans(monkeypatch)
    payload = HookPayload.from_stdin_json('PermissionRequest', {
        'hook_event_name': 'PermissionRequest', 'session_id': 's1',
        'tool_name': 'Bash', 'tool_input': {'command': 'ls'},
        'tool_use_id': 'toolu_perm12345678',
    })
    permission_events.handle_request(payload)
    assert len(spans) == 1
    s = spans[0]
    assert s['name'] == 'permission.request'
    assert s['status_code'] == 'PENDING'
    assert s['span_id'] == perm_pending_id('toolu_perm12345678')
    assert s['span_id'].startswith('permreq-')


def test_permission_request_without_tool_use_id_keeps_random_id(monkeypatch):
    spans = _capture_spans(monkeypatch)
    payload = HookPayload.from_stdin_json('PermissionRequest', {
        'hook_event_name': 'PermissionRequest', 'session_id': 's1',
        'tool_name': 'Bash', 'tool_input': {'command': 'ls'},
    })
    permission_events.handle_request(payload)
    assert spans[0]['status_code'] == 'PENDING'
    assert spans[0]['span_id'] is None  # degrades to random server-side id


def test_permission_denied_is_error_status(monkeypatch):
    spans = _capture_spans(monkeypatch)
    payload = HookPayload.from_stdin_json('PermissionDenied', {
        'hook_event_name': 'PermissionDenied', 'session_id': 's1',
        'tool_name': 'Bash', 'tool_input': {'command': 'ls'},
        'tool_use_id': 'toolu_perm12345678',
    })
    permission_events.handle_denied(payload)
    assert spans[0]['name'] == 'permission.denied'
    assert spans[0]['status_code'] == 'ERROR'
