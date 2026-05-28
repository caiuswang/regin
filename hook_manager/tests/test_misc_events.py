"""Tests for misc_events: TeammateIdle, InstructionsLoaded, ConfigChange, WorktreeRemove.

Silent-trace policy (commit fa3922e): handlers return
`HookResponse(suppress_output=True)` with no additional_context and emit
a trace span for the trace dashboard instead.
"""

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import misc_events


def _p(event, **kw):
    return HookPayload.from_stdin_json(event, {'hook_event_name': event, **kw})


@pytest.fixture
def captured_spans(monkeypatch):
    import lib.hook_plugin as hp
    spans: list[dict] = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    return spans


def test_teammate_idle_names_the_teammate(captured_spans):
    misc_events.teammate_idle(_p('TeammateIdle', session_id='s1', teammate_name='alice'))
    s = captured_spans[0]
    assert s['name'] == 'teammate.idle'
    assert s['attributes']['teammate_name'] == 'alice'


def test_teammate_idle_default_is_unknown(captured_spans):
    misc_events.teammate_idle(_p('TeammateIdle', session_id='s1'))
    assert captured_spans[0]['attributes']['teammate_name'] == 'unknown'


def test_instructions_loaded_fields(captured_spans):
    misc_events.instructions_loaded(_p('InstructionsLoaded', session_id='s1',
        file_path='/tmp/CLAUDE.md',
        memory_type='Project',
        load_reason='session_start'))
    s = captured_spans[0]
    assert s['name'] == 'instructions.loaded'
    assert s['attributes']['file_path'] == '/tmp/CLAUDE.md'
    assert s['attributes']['memory_type'] == 'Project'
    assert s['attributes']['load_reason'] == 'session_start'


def test_config_change_includes_source(captured_spans):
    misc_events.config_change(_p('ConfigChange', session_id='s1', config_source='user_settings'))
    s = captured_spans[0]
    assert s['name'] == 'config.change'
    assert s['attributes']['source'] == 'user_settings'


def test_worktree_remove_includes_path(captured_spans):
    misc_events.worktree_remove(_p('WorktreeRemove', session_id='s1',
        worktree_path='/tmp/my-worktree'))
    s = captured_spans[0]
    assert s['name'] == 'worktree.remove'
    assert s['attributes']['path'] == '/tmp/my-worktree'


# ── Field-name aliasing (newer Claude Code payloads use shorter names) ─

def test_config_change_accepts_source_alias(captured_spans):
    """Claude Code sends either `config_source` (legacy) or `source`.
    Without the alias, half the payloads would leave the span unlabelled."""
    misc_events.config_change(_p('ConfigChange', session_id='s1', source='project_claude_md'))
    assert captured_spans[0]['attributes']['source'] == 'project_claude_md'


def test_worktree_remove_accepts_path_alias(captured_spans):
    """Same aliasing story: `worktree_path` (legacy) → `path`."""
    misc_events.worktree_remove(_p('WorktreeRemove', session_id='s1',
        path='/var/tmp/ephemeral'))
    assert captured_spans[0]['attributes']['path'] == '/var/tmp/ephemeral'


# ── Missing-field behavior: span still posted, attribute omitted ──────

def test_config_change_missing_source_omits_attribute(captured_spans):
    """No source on the payload → no `source` attribute on the span.
    Empty-string `source` would break dashboards that filter on it."""
    misc_events.config_change(_p('ConfigChange', session_id='s1'))
    attrs = captured_spans[0]['attributes']
    assert 'source' not in attrs


def test_worktree_remove_missing_path_omits_attribute(captured_spans):
    misc_events.worktree_remove(_p('WorktreeRemove', session_id='s1'))
    attrs = captured_spans[0]['attributes']
    assert 'path' not in attrs


def test_instructions_loaded_partial_fields(captured_spans):
    """Only `file_path` present — other two omitted. Without this test, a
    regression that short-circuits on the first missing field would
    silently drop the file_path attribute too."""
    misc_events.instructions_loaded(_p('InstructionsLoaded', session_id='s1',
        file_path='/tmp/CLAUDE.md'))
    attrs = captured_spans[0]['attributes']
    assert attrs['file_path'] == '/tmp/CLAUDE.md'
    assert 'memory_type' not in attrs
    assert 'load_reason' not in attrs


# ── Response contract: silent-trace policy ────────────────────────────

def test_all_handlers_return_suppress_output_no_context(captured_spans):
    """Silent-trace policy: every misc_events handler must return
    suppress_output=True and NO additional_context. A regression that
    starts echoing instructions-loaded or worktree events into the
    transcript would be spammy and user-visible."""
    for fn, ev in [
        (misc_events.teammate_idle, 'TeammateIdle'),
        (misc_events.instructions_loaded, 'InstructionsLoaded'),
        (misc_events.config_change, 'ConfigChange'),
        (misc_events.worktree_remove, 'WorktreeRemove'),
    ]:
        r = fn(_p(ev, session_id='s1'))
        assert r is not None
        assert r.suppress_output is True
        assert r.additional_context is None


# ── _safe_emit swallows ingest errors ─────────────────────────────────

def test_safe_emit_swallows_post_span_errors(monkeypatch):
    """If the trace ingest explodes (server down, schema mismatch), the
    handler must still return its suppress_output response — not crash
    the hook process and leave the model with no response."""
    def _boom(**_kw):
        raise RuntimeError('ingest down')
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_span', _boom)

    # Exercise every handler through its _safe_emit path.
    for fn, ev in [
        (misc_events.teammate_idle, 'TeammateIdle'),
        (misc_events.instructions_loaded, 'InstructionsLoaded'),
        (misc_events.config_change, 'ConfigChange'),
        (misc_events.worktree_remove, 'WorktreeRemove'),
    ]:
        r = fn(_p(ev, session_id='s1'))
        assert r is not None
        assert r.suppress_output is True
