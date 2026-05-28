"""Tests for lib.hook_plugin.HookContext.

HookContext is the convenience wrapper that every legacy hook script once
used; the hook_manager refactor routes most events through handlers instead,
but the HookContext API is still exposed for the skill-read tracer and any
one-off scripts. Pin its behaviors so future hook_plugin changes don't
silently break script callers.
"""

from __future__ import annotations

import io
import json
import os
import sys

from lib.hook_plugin import HookContext


def _stdin(monkeypatch, data):
    """Install a StringIO on sys.stdin containing `data` (dict → JSON)."""
    buf = io.StringIO(json.dumps(data) if isinstance(data, (dict, list)) else data)
    monkeypatch.setattr(sys, 'stdin', buf)


# ── Payload parsing ──────────────────────────────────────────────────

def test_parses_top_level_fields(monkeypatch):
    _stdin(monkeypatch, {
        'hook_event_name': 'PreToolUse',
        'session_id': 'abc',
        'tool_name': 'Read',
        'tool_input': {'file_path': '/tmp/a.txt'},
        'tool_response': {'filePath': '/tmp/a.txt'},
    })
    ctx = HookContext()
    assert ctx.hook_event == 'PreToolUse'
    assert ctx.session_id == 'abc'
    assert ctx.tool_name == 'Read'
    assert ctx.tool_input == {'file_path': '/tmp/a.txt'}
    assert ctx.tool_response == {'filePath': '/tmp/a.txt'}
    assert ctx.skipped is False


def test_none_tool_input_and_response_become_dicts(monkeypatch):
    """Claude Code sometimes sends literal JSON null for these fields.
    Users of HookContext depend on them being dicts so `.get()` works —
    passing None through would crash every downstream callsite."""
    _stdin(monkeypatch, {
        'hook_event_name': 'PreToolUse',
        'tool_input': None,
        'tool_response': None,
    })
    ctx = HookContext()
    assert ctx.tool_input == {}
    assert ctx.tool_response == {}


def test_malformed_json_yields_empty_payload(monkeypatch):
    """Garbage on stdin → empty dict, not a crash. Every derived field
    falls back to its default (None / '' / {})."""
    _stdin(monkeypatch, 'not json at all')
    ctx = HookContext()
    assert ctx.payload == {}
    assert ctx.hook_event is None
    assert ctx.tool_name is None
    assert ctx.prompt == ''
    assert ctx.tool_input == {}


# ── expected_event skip gate ─────────────────────────────────────────

def test_expected_event_mismatch_sets_skipped(monkeypatch):
    """Scripts pin themselves to a single event via `expected_event`.
    If the caller fires on a different event (misconfigured
    settings.json), the context marks skipped=True and the script's
    early-return path kicks in."""
    _stdin(monkeypatch, {'hook_event_name': 'PostToolUse'})
    ctx = HookContext(expected_event='PreToolUse')
    assert ctx.skipped is True
    assert ctx.hook_event == 'PostToolUse'


def test_expected_event_match_leaves_skipped_false(monkeypatch):
    _stdin(monkeypatch, {'hook_event_name': 'PreToolUse'})
    ctx = HookContext(expected_event='PreToolUse')
    assert ctx.skipped is False


def test_expected_event_none_never_skips(monkeypatch):
    """No expected_event → handler accepts any event; skipped must
    stay False regardless of hook_event_name."""
    _stdin(monkeypatch, {'hook_event_name': 'AnythingGoes'})
    ctx = HookContext()  # no expected_event
    assert ctx.skipped is False


# ── Prompt extraction (HookContext mirrors core._extract_prompt) ─────

def test_prompt_priority_chain(monkeypatch):
    _stdin(monkeypatch, {
        'hook_event_name': 'UserPromptSubmit',
        'text': 'from text',
        'message': 'from message',
    })
    ctx = HookContext()
    # text wins when prompt is absent.
    assert ctx.prompt == 'from text'


def test_prompt_strips_whitespace(monkeypatch):
    _stdin(monkeypatch, {'hook_event_name': 'UserPromptSubmit',
                          'prompt': '   hello  \n'})
    assert HookContext().prompt == 'hello'


def test_prompt_ignores_non_string_values(monkeypatch):
    """Same discipline as core: numbers/bools in the candidate fields
    are skipped over, never coerced."""
    _stdin(monkeypatch, {'hook_event_name': 'UserPromptSubmit',
                          'prompt': 42, 'text': True, 'message': 'real'})
    assert HookContext().prompt == 'real'


def test_prompt_empty_when_no_candidates(monkeypatch):
    _stdin(monkeypatch, {'hook_event_name': 'UserPromptSubmit'})
    assert HookContext().prompt == ''


# ── Span helpers (thin wrappers around lib.trace.trace_context) ────────────

def test_start_end_span_round_trip_via_context(monkeypatch, tmp_path):
    """HookContext.start_span / end_span delegate to lib.trace.trace_context.
    Isolate that module's state in tmp_path so real ~/.claude/traces
    is untouched."""
    from lib.trace import trace_context
    monkeypatch.setattr(trace_context, 'TRACE_DIR', str(tmp_path))

    _stdin(monkeypatch, {'hook_event_name': 'PreToolUse',
                          'session_id': 'ctx-session'})
    ctx = HookContext()
    outer = ctx.start_span('outer')
    inner = ctx.start_span('inner')
    assert inner['parent_id'] == outer['span_id']

    ended = ctx.end_span('inner')
    assert ended and ended['name'] == 'inner'
    assert ctx.current_span()['name'] == 'outer'


def test_pop_all_spans_returns_list(monkeypatch, tmp_path):
    from lib.trace import trace_context
    monkeypatch.setattr(trace_context, 'TRACE_DIR', str(tmp_path))

    _stdin(monkeypatch, {'hook_event_name': 'PreToolUse',
                          'session_id': 'pop-session'})
    ctx = HookContext()
    ctx.start_span('a')
    ctx.start_span('b')
    popped = ctx.pop_all_spans()
    assert {s['name'] for s in popped} == {'a', 'b'}
    assert ctx.current_span() is None


# ── post_span: parent_id auto-resolution from active stack ───────────

def test_post_span_auto_parents_to_current_active_span(monkeypatch, tmp_path):
    """When HookContext.post_span is called without parent_id and an
    active span exists on the stack, the posted span is parented under
    it. Without this, every posted span would be an orphan root even
    when the caller had already opened a conversation/prompt span."""
    from lib import hook_plugin as hp
    from lib.trace import trace_context
    monkeypatch.setattr(trace_context, 'TRACE_DIR', str(tmp_path))

    posted: list[dict] = []
    monkeypatch.setattr(hp, 'hook_plugin_post_span',
                        lambda **kw: posted.append(kw))

    _stdin(monkeypatch, {'hook_event_name': 'PreToolUse',
                          'session_id': 'auto-parent'})
    ctx = HookContext()
    outer = ctx.start_span('outer')
    ctx.post_span(name='child', attributes={'a': 1})
    assert len(posted) == 1
    assert posted[0]['parent_id'] == outer['span_id']
    assert posted[0]['name'] == 'child'


def test_post_span_explicit_parent_overrides_stack(monkeypatch, tmp_path):
    """An explicit parent_id must beat the auto-lookup. Not testing
    this would let a refactor silently flip the precedence."""
    from lib import hook_plugin as hp
    from lib.trace import trace_context
    monkeypatch.setattr(trace_context, 'TRACE_DIR', str(tmp_path))

    posted: list[dict] = []
    monkeypatch.setattr(hp, 'hook_plugin_post_span',
                        lambda **kw: posted.append(kw))

    _stdin(monkeypatch, {'hook_event_name': 'PreToolUse',
                          'session_id': 'explicit-parent'})
    ctx = HookContext()
    ctx.start_span('outer')
    ctx.post_span(name='child', parent_id='explicit-xyz')
    assert posted[0]['parent_id'] == 'explicit-xyz'


def test_post_span_explicit_none_parent_is_root(monkeypatch, tmp_path):
    """parent_id=None (as opposed to the _UNSET sentinel) means root.
    The auto-lookup must only kick in when the caller didn't supply
    parent_id at all — _UNSET is the discriminator."""
    from lib import hook_plugin as hp
    from lib.trace import trace_context
    monkeypatch.setattr(trace_context, 'TRACE_DIR', str(tmp_path))

    posted: list[dict] = []
    monkeypatch.setattr(hp, 'hook_plugin_post_span',
                        lambda **kw: posted.append(kw))

    _stdin(monkeypatch, {'hook_event_name': 'PreToolUse',
                          'session_id': 's'})
    ctx = HookContext()
    ctx.start_span('outer')  # active span exists, but…
    ctx.post_span(name='child', parent_id=None)  # explicit None → root
    assert posted[0]['parent_id'] is None


# ── emit: response JSON shape ────────────────────────────────────────

def test_emit_writes_json_with_hook_specific_output(monkeypatch, capsys):
    _stdin(monkeypatch, {'hook_event_name': 'PreToolUse'})
    ctx = HookContext()
    ctx.emit('PreToolUse', 'custom context')
    captured = capsys.readouterr().out
    obj = json.loads(captured.strip())
    assert obj['hookSpecificOutput']['hookEventName'] == 'PreToolUse'
    assert obj['hookSpecificOutput']['additionalContext'] == 'custom context'
    assert obj['suppressOutput'] is True


def test_emit_suppress_output_false_allows_visible_output(monkeypatch, capsys):
    """Callers can opt into visible output by passing suppress_output=False.
    Normally hooks stay quiet but e.g. an error path might want to surface
    text to the transcript."""
    _stdin(monkeypatch, {'hook_event_name': 'PreToolUse'})
    ctx = HookContext()
    ctx.emit('PreToolUse', 'visible!', suppress_output=False)
    obj = json.loads(capsys.readouterr().out.strip())
    assert obj['suppressOutput'] is False
