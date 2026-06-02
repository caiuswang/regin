"""Tests for the iteration-2 handlers: subagent, compact, task.

Silent-trace policy (commit fa3922e): subagent / compact / task handlers
return `HookResponse(suppress_output=True)` with no additional_context and
emit a trace span instead.
"""

import json

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import (
    compact_lifecycle,
    post_tool_failure,
    post_tool_trace,
    pre_tool_trace,
    subagent_lifecycle,
    task_lifecycle,
)


def _p(event, **kw):
    return HookPayload.from_stdin_json(event, {'hook_event_name': event, **kw})


@pytest.fixture
def captured_spans(monkeypatch):
    """Stub lib.hook_plugin.post_span; return the list it collects into."""
    import lib.hook_plugin as hp
    spans: list[dict] = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    return spans


# ── subagent_lifecycle ────────────────────────────────────────────────

def test_subagent_start_span_carries_type_and_id(captured_spans):
    subagent_lifecycle.handle_start(_p('SubagentStart', session_id='s1',
        agent_type='Explore', agent_id='abcdef1234567890'))
    assert len(captured_spans) == 1
    s = captured_spans[0]
    assert s['name'] == 'subagent.start'
    assert s['attributes']['agent_type'] == 'Explore'
    assert s['attributes']['agent_id'] == 'abcdef1234567890'


def test_subagent_start_handles_missing_fields(captured_spans):
    subagent_lifecycle.handle_start(_p('SubagentStart', session_id='s1'))
    assert len(captured_spans) == 1
    assert captured_spans[0]['name'] == 'subagent.start'
    # No required keys when payload is empty — still posts a span for the boundary
    assert 'agent_type' not in captured_spans[0]['attributes']


def test_subagent_stop_truncates_result_preview(captured_spans):
    long = 'line1\n' + ('x' * 500)
    subagent_lifecycle.handle_stop(_p('SubagentStop', session_id='s1',
        agent_type='Plan', last_assistant_message=long))
    s = captured_spans[0]
    assert s['name'] == 'subagent.stop'
    preview = s['attributes']['result_preview']
    assert '\n' not in preview  # flattened
    assert preview.endswith('…')
    assert len(preview) <= subagent_lifecycle._RESULT_PREVIEW_MAX + 1
    assert s['attributes']['agent_type'] == 'Plan'


def test_subagent_accepts_subagent_field_aliases(captured_spans):
    """Claude Code has used both `agent_*` and `subagent_*` field names.
    The handler aliases both to the same span attributes. Without this
    test, a one-sided refactor could drop either branch undetected."""
    subagent_lifecycle.handle_start(_p('SubagentStart', session_id='s1',
        subagent_type='Explore',
        subagent_id='id-xyz',
        subagent_name='scout-1'))
    s = captured_spans[0]
    assert s['attributes']['agent_type'] == 'Explore'
    assert s['attributes']['agent_id'] == 'id-xyz'
    assert s['attributes']['agent_name'] == 'scout-1'


def test_subagent_captures_description(captured_spans):
    """The `description` field (short task summary) is what trace viewers
    show next to each subagent span — losing it would reduce every
    subagent to 'unnamed task'."""
    subagent_lifecycle.handle_start(_p('SubagentStart', session_id='s1',
        agent_type='Explore', description='find old hook scripts'))
    assert captured_spans[0]['attributes']['description'] == 'find old hook scripts'


def test_subagent_short_result_is_not_truncated(captured_spans):
    """Short messages must be copied verbatim — no ellipsis, no loss.
    Truncation-only tests would miss a bug that unconditionally trimmed
    the last character off every preview."""
    subagent_lifecycle.handle_stop(_p('SubagentStop', session_id='s1',
        agent_type='Explore', last_assistant_message='done'))
    preview = captured_spans[0]['attributes']['result_preview']
    assert preview == 'done'


def test_subagent_handlers_return_suppress_output():
    """Silent-trace policy: both start and stop must return
    suppress_output=True and NO additional_context."""
    # SubagentStop requires agent identity to pass the phantom-event gate;
    # the silent-trace contract still holds whether we emit a span or not.
    for fn, ev, extra in [
        (subagent_lifecycle.handle_start, 'SubagentStart', {}),
        (subagent_lifecycle.handle_stop, 'SubagentStop', {'agent_type': 'Explore'}),
    ]:
        r = fn(_p(ev, session_id='s1', **extra))
        assert r is not None
        assert r.suppress_output is True
        assert r.additional_context is None


# ── subagent assistant_response spans ─────────────────────────────────

def _write_jsonl(path, entries):
    path.write_text('\n'.join(json.dumps(e) for e in entries) + '\n')


def _subagent_assistant(*, msg_id='m1', uuid='sa-turn-uuid-abcde',
                        text='subagent says hi', ts='2026-04-27T12:00:00Z',
                        extra_blocks=None, model='claude-haiku-4-5-20251001'):
    content = [{'type': 'text', 'text': text}]
    if extra_blocks:
        content.extend(extra_blocks)
    return {
        'type': 'assistant',
        'uuid': uuid,
        'parentUuid': None,
        'timestamp': ts,
        'requestId': 'req-' + msg_id,
        'message': {
            'id': msg_id,
            'model': model,
            'content': content,
            'usage': {
                'input_tokens': 10,
                'output_tokens': 5,
                'cache_creation_input_tokens': 0,
                'cache_read_input_tokens': 0,
            },
        },
    }


def test_subagent_stop_emits_assistant_response_spans(captured_spans, tmp_path):
    transcript = tmp_path / 'agent.jsonl'
    _write_jsonl(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _subagent_assistant(text='hello from subagent',
                            uuid='sa-turn-uuid-zzz123'),
    ])
    subagent_lifecycle.handle_stop(_p('SubagentStop',
        session_id='parent-sid',
        agent_id='a1d366b4ecdd50e96',
        agent_transcript_path=str(transcript)))
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert len(response_spans) == 1
    s = response_spans[0]
    assert s['trace_id'] == 'parent-sid'
    assert s['span_id'] == 'resp-sa-' + 'sa-turn-uuid-zzz123'[:13]
    assert s.get('parent_id') is None  # graft handles nesting via agent_id
    assert s['attributes']['agent_id'] == 'a1d366b4ecdd50e96'
    assert s['attributes']['text'] == 'hello from subagent'
    assert s['attributes']['turn_uuid'] == 'sa-turn-uuid-zzz123'


def test_subagent_stop_skips_when_capture_disabled(captured_spans, tmp_path, monkeypatch):
    from lib import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, 'capture_assistant_response', False)

    transcript = tmp_path / 'agent.jsonl'
    _write_jsonl(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _subagent_assistant(text='hello'),
    ])
    subagent_lifecycle.handle_stop(_p('SubagentStop',
        session_id='parent-sid',
        agent_id='aid',
        agent_transcript_path=str(transcript)))
    names = [s.get('name') for s in captured_spans]
    assert 'subagent.stop' in names
    assert 'assistant_response' not in names


def test_subagent_stop_no_path_or_unreadable_is_safe(captured_spans, tmp_path):
    # Same shape as before but with a non-empty agent_type so the
    # phantom-event gate doesn't kick in — this test verifies that the
    # handler still tolerates a missing/absent transcript path.
    subagent_lifecycle.handle_stop(_p('SubagentStop',
        session_id='parent-sid', agent_id='aid', agent_type='Explore'))
    subagent_lifecycle.handle_stop(_p('SubagentStop',
        session_id='parent-sid', agent_id='aid', agent_type='Explore',
        agent_transcript_path=str(tmp_path / 'missing.jsonl')))
    names = [s.get('name') for s in captured_spans]
    assert names.count('subagent.stop') == 2
    assert 'assistant_response' not in names


# ── workflow-subagent gate ────────────────────────────────────────────
# A background Workflow run fires the FULL hook suite into the LAUNCHING
# session for every one of its agents (all tagged agent_type='workflow-subagent').
# That run is captured independently as its own wf_ session, so re-recording the
# tool/turn activity off these hooks just duplicates it and floods the launching
# conversation. The trace handlers gate on HookPayload.is_workflow_subagent:
# tool/turn emission is dropped, the lightweight start/stop markers survive.

def test_post_tool_trace_skips_workflow_subagent(captured_spans):
    post_tool_trace.handle(_p('PostToolUse', session_id='s1', tool_name='WebFetch',
                              agent_id='aWF', agent_type='workflow-subagent',
                              tool_input={'url': 'https://x'}))
    assert captured_spans == []


def test_pre_tool_trace_skips_workflow_subagent(captured_spans):
    pre_tool_trace.handle(_p('PreToolUse', session_id='s1', tool_name='WebFetch',
                             agent_id='aWF', agent_type='workflow-subagent',
                             tool_use_id='toolu_x'))
    assert captured_spans == []


def test_post_tool_failure_skips_workflow_subagent(captured_spans):
    r = post_tool_failure.handle(_p('PostToolUseFailure', session_id='s1',
                                    tool_name='Read', agent_id='aWF',
                                    agent_type='workflow-subagent',
                                    error='boom'))
    assert captured_spans == []
    assert r.suppress_output is True
    assert r.additional_context is None  # no model-facing failure hint either


def test_normal_subagent_tool_still_emitted(captured_spans):
    # A real Task subagent (agent_type='Explore') is NOT gated — its tool span
    # must still land so the projection can nest it under its subagent.start.
    post_tool_trace.handle(_p('PostToolUse', session_id='s1', tool_name='Read',
                              agent_id='aReal', agent_type='Explore',
                              tool_input={'file_path': '/x'}))
    assert [s['name'] for s in captured_spans] == ['tool.Read']
    assert captured_spans[0]['attributes']['agent_id'] == 'aReal'


def test_subagent_stop_workflow_keeps_marker_skips_responses(captured_spans, tmp_path):
    # The workflow-subagent's transcript exists and has turns, but the response
    # mirror is skipped; only the subagent.stop marker survives.
    transcript = tmp_path / 'agent.jsonl'
    _write_jsonl(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _subagent_assistant(text='workflow agent output', uuid='sa-wf-uuid-xyz999'),
    ])
    subagent_lifecycle.handle_stop(_p('SubagentStop',
        session_id='parent-sid', agent_id='aWF', agent_type='workflow-subagent',
        agent_transcript_path=str(transcript)))
    names = [s.get('name') for s in captured_spans]
    assert 'subagent.stop' in names
    assert 'assistant_response' not in names


def test_subagent_stop_skips_phantom_event(captured_spans, tmp_path):
    """Claude Code occasionally fires SubagentStop with only an
    agent_id (no agent_type, no agent_name) and a transcript path that
    doesn't exist. We must NOT emit a span for those — they're ghost
    events whose `last_assistant_message` text isn't tied to anything
    real in the session.
    """
    subagent_lifecycle.handle_stop(_p('SubagentStop',
        session_id='parent-sid',
        agent_id='a63c248e1d3085cf1',
        agent_type='',
        agent_transcript_path=str(tmp_path / 'agent-a63c248e1d3085cf1.jsonl'),
        last_assistant_message='rebuild the frontend and check it'))
    assert captured_spans == []


def test_subagent_stop_emits_thinking_only_turn(captured_spans, tmp_path):
    """A subagent turn that produced only thinking blocks (no
    user-visible text) leaves a distinct `assistant.thinking` span so
    the conversation view doesn't render an empty response card, but
    the trace timeline can still surface that reasoning happened.
    Pre-split, this turn would have been silently dropped (since
    `turn.text` was empty)."""
    transcript = tmp_path / 'agent.jsonl'
    turn_uuid = 'sa-think-only-uuid1'
    _write_jsonl(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _subagent_assistant(
            uuid=turn_uuid,
            text='',  # no visible text
            extra_blocks=[
                {'type': 'thinking', 'thinking': '', 'signature': 'z' * 256},
                {'type': 'tool_use', 'id': 'tu_only', 'name': 'Bash', 'input': {}},
            ],
        ),
    ])
    subagent_lifecycle.handle_stop(_p('SubagentStop',
        session_id='parent-sid',
        agent_id='aid',
        agent_type='Explore',
        agent_transcript_path=str(transcript)))
    think_spans = [s for s in captured_spans if s.get('name') == 'assistant.thinking']
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert response_spans == []
    assert len(think_spans) == 1
    attrs = think_spans[0]['attributes']
    assert attrs['thinking_blocks'] == 1
    assert attrs['thinking_signature_bytes'] == 256
    assert 'text' not in attrs
    assert think_spans[0]['span_id'].startswith('think-sa-')


def test_subagent_stop_propagates_tool_calls(captured_spans, tmp_path):
    transcript = tmp_path / 'agent.jsonl'
    turn_uuid = 'sa-turn-uuid-tools1'
    _write_jsonl(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _subagent_assistant(
            uuid=turn_uuid,
            text='running a tool',
            extra_blocks=[{'type': 'tool_use', 'id': 'tu1', 'name': 'Bash', 'input': {}}],
        ),
        # tool_result on a follow-up user entry — patches is_error onto the turn
        {'type': 'user', 'uuid': 'u2', 'parentUuid': turn_uuid,
         'message': {'content': [
             {'type': 'tool_result', 'tool_use_id': 'tu1', 'is_error': True,
              'content': 'boom'},
         ]}},
    ])
    subagent_lifecycle.handle_stop(_p('SubagentStop',
        session_id='parent-sid',
        agent_id='aid',
        agent_transcript_path=str(transcript)))
    response_spans = [s for s in captured_spans if s.get('name') == 'assistant_response']
    assert len(response_spans) == 1
    tool_calls = response_spans[0]['attributes'].get('tool_calls')
    assert tool_calls == [{'name': 'Bash', 'is_error': True}]


# ── compact_lifecycle ─────────────────────────────────────────────────

def test_pre_compact_records_trigger(captured_spans):
    compact_lifecycle.handle_pre(_p('PreCompact', session_id='s1',
        compaction_trigger='auto'))
    s = captured_spans[0]
    assert s['name'] == 'compact.pre'
    assert s['attributes']['trigger'] == 'auto'


def test_post_compact_records_trigger(captured_spans):
    compact_lifecycle.handle_post(_p('PostCompact', session_id='s1',
        compaction_trigger='manual'))
    s = captured_spans[0]
    assert s['name'] == 'compact.post'
    assert s['attributes']['trigger'] == 'manual'


def test_pre_compact_captures_custom_instructions(captured_spans):
    compact_lifecycle.handle_pre(_p('PreCompact', session_id='s1',
        trigger='manual',
        custom_instructions='remove all hooks related info, only keep the refactor plan'))
    attrs = captured_spans[0]['attributes']
    assert attrs['trigger'] == 'manual'
    assert attrs['custom_instructions'] == 'remove all hooks related info, only keep the refactor plan'


def test_pre_compact_truncates_long_instructions(captured_spans):
    long = 'x' * (compact_lifecycle._INSTRUCTIONS_MAX + 500)
    compact_lifecycle.handle_pre(_p('PreCompact', session_id='s1',
        trigger='manual', custom_instructions=long))
    attrs = captured_spans[0]['attributes']
    assert attrs['custom_instructions'].endswith('…')
    assert len(attrs['custom_instructions']) == compact_lifecycle._INSTRUCTIONS_MAX + 1


def test_post_compact_captures_summary(captured_spans):
    compact_lifecycle.handle_post(_p('PostCompact', session_id='s1',
        trigger='manual', compact_summary='<summary>stuff</summary>'))
    attrs = captured_spans[0]['attributes']
    assert attrs['summary'] == '<summary>stuff</summary>'
    assert attrs['summary_chars'] == len('<summary>stuff</summary>')


def test_compact_never_blocks(captured_spans):
    r = compact_lifecycle.handle_pre(_p('PreCompact', session_id='s1'))
    assert r is not None
    assert r.decision is None
    assert r.permission_decision is None


def test_pre_compact_skips_whitespace_only_instructions(captured_spans):
    """`custom_instructions` of only spaces/tabs is effectively empty —
    don't stamp the span with a blank attribute (noise in the trace
    view; also confuses downstream aggregators that count non-empty
    instructions)."""
    compact_lifecycle.handle_pre(_p('PreCompact', session_id='s1',
        trigger='manual', custom_instructions='   \t\n   '))
    attrs = captured_spans[0]['attributes']
    assert 'custom_instructions' not in attrs


def test_post_compact_stores_full_summary(captured_spans):
    """Unlike custom_instructions, the model-generated summary is stored
    in FULL (no truncation) — it's the recoverable conversation state and
    the whole point of capturing the compaction. summary_chars mirrors the
    full length."""
    long = 'y' * (compact_lifecycle._INSTRUCTIONS_MAX + 200)
    compact_lifecycle.handle_post(_p('PostCompact', session_id='s1',
        compaction_trigger='auto', compact_summary=long))
    attrs = captured_spans[0]['attributes']
    assert attrs['summary'] == long
    assert not attrs['summary'].endswith('…')
    assert attrs['summary_chars'] == len(long)


def test_compact_missing_trigger_omits_attribute(captured_spans):
    """No trigger on the payload → no `trigger` attribute on the span.
    Setting it to the empty string would break trace queries that filter
    `trigger in ('auto','manual')`."""
    compact_lifecycle.handle_pre(_p('PreCompact', session_id='s1'))
    attrs = captured_spans[0]['attributes']
    assert 'trigger' not in attrs


# ── task_lifecycle ────────────────────────────────────────────────────

def test_task_created_truncates_long_subject(captured_spans):
    long_subj = 'x' * 300
    task_lifecycle.handle_created(_p('TaskCreated', session_id='s1',
        task_id='task_abcd1234efgh5678', task_subject=long_subj))
    s = captured_spans[0]
    assert s['name'] == 'task.created'
    assert s['attributes']['task_id'] == 'task_abcd1234efgh5678'
    assert s['attributes']['subject'] == ('x' * 60) + '…'
    assert s['attributes']['subject_chars'] == 300


def test_task_completed_includes_subject(captured_spans):
    task_lifecycle.handle_completed(_p('TaskCompleted', session_id='s1',
        task_id='t1', task_subject='Run tests'))
    s = captured_spans[0]
    assert s['name'] == 'task.completed'
    assert s['attributes']['subject'] == 'Run tests'


def test_task_never_blocks(captured_spans):
    # Spec allows TaskCreated → block to roll back the task creation; our
    # policy is to never do that (blocking user intent is user-hostile).
    r = task_lifecycle.handle_created(_p('TaskCreated', session_id='s1',
        task_id='x', task_subject='y'))
    assert r is not None
    assert r.decision is None


def test_task_nested_task_object_is_accepted(captured_spans):
    """Claude Code sends task fields two ways: flat (task_id/task_subject)
    and nested under a `task` object. The handler must accept both —
    without the nested branch, every task on the newer schema would
    leave its span unlabelled."""
    task_lifecycle.handle_created(_p('TaskCreated', session_id='s1',
        task={'id': 'nested-id', 'subject': 'nested subject', 'status': 'pending'}))
    s = captured_spans[0]
    assert s['attributes']['task_id'] == 'nested-id'
    assert s['attributes']['subject'] == 'nested subject'
    assert s['attributes']['status'] == 'pending'


def test_task_records_status_on_completion(captured_spans):
    """`status` is what distinguishes completed from deleted/failed in
    the trace view. Without capturing it, every terminal state renders
    identically."""
    task_lifecycle.handle_completed(_p('TaskCompleted', session_id='s1',
        task_id='t1', task_subject='ship it', status='completed'))
    assert captured_spans[0]['attributes']['status'] == 'completed'


def test_task_short_subject_is_not_truncated(captured_spans):
    """Subjects under the 60-char cap must be copied verbatim and
    subject_chars must match their length. Mis-truncation would silently
    corrupt every short task label."""
    task_lifecycle.handle_created(_p('TaskCreated', session_id='s1',
        task_id='t', task_subject='Add test'))
    attrs = captured_spans[0]['attributes']
    assert attrs['subject'] == 'Add test'
    assert attrs['subject_chars'] == len('Add test')
    assert '…' not in attrs['subject']




def test_emit_subagent_responses_seen_gating(captured_spans, tmp_path):
    """The live rescan calls emit_subagent_responses with a seen-uuid set so a
    running subagent's turns post once, not on every poll. A seen uuid is
    skipped; a fresh one posts."""
    from hook_manager.handlers.subagent_lifecycle import emit_subagent_responses
    transcript = tmp_path / 'agent.jsonl'
    _write_jsonl(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _subagent_assistant(text='one', uuid='sa-uuid-aaaaaaaa', msg_id='m1'),
        _subagent_assistant(text='two', uuid='sa-uuid-bbbbbbbb', msg_id='m2',
                            ts='2026-04-27T12:00:01Z'),
    ])
    emit_subagent_responses('sid', str(transcript), 'agent-x',
                            seen={'sa-uuid-aaaaaaaa'})
    posted = {s['attributes']['turn_uuid'] for s in captured_spans
              if s.get('name') == 'assistant_response'}
    assert posted == {'sa-uuid-bbbbbbbb'}  # the seen turn was skipped


def test_emit_subagent_responses_no_seen_posts_all(captured_spans, tmp_path):
    from hook_manager.handlers.subagent_lifecycle import emit_subagent_responses
    transcript = tmp_path / 'agent.jsonl'
    _write_jsonl(transcript, [
        {'type': 'user', 'uuid': 'u1', 'message': {'content': 'go'}},
        _subagent_assistant(text='one', uuid='sa-uuid-cccccccc', msg_id='m1'),
        _subagent_assistant(text='two', uuid='sa-uuid-dddddddd', msg_id='m2',
                            ts='2026-04-27T12:00:01Z'),
    ])
    emit_subagent_responses('sid', str(transcript), 'agent-y')  # seen=None
    posted = {s['attributes']['turn_uuid'] for s in captured_spans
              if s.get('name') == 'assistant_response'}
    assert posted == {'sa-uuid-cccccccc', 'sa-uuid-dddddddd'}
