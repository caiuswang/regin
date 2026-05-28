"""Tests for plan_trace handler.

Silent-trace policy (commit fa3922e): plan_trace emits a `plan.exit` span
tagged with the plan filename instead of leaking `plan='…'` into the
model's transcript on every ExitPlanMode.
"""

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import plan_trace


def _p(event, **kw):
    return HookPayload.from_stdin_json(event, {'hook_event_name': event, **kw})


class _FakeProvider:
    def __init__(self, plans_dir):
        self._plans_dir = plans_dir

    def plans_dir(self):
        return self._plans_dir


def _payload_with_provider(event, plans_dir, **kw):
    payload = _p(event, **kw)
    payload.__dict__['resolved_provider'] = _FakeProvider(plans_dir)
    return payload


@pytest.fixture
def captured_spans(monkeypatch):
    import lib.hook_plugin as hp
    spans: list[dict] = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    return spans


@pytest.fixture
def captured_events(monkeypatch):
    import lib.hook_plugin as hp
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(hp, 'post_event',
                        lambda name, data: events.append((name, data)))
    return events


def test_ignores_non_exit_plan_tools(captured_spans):
    r = plan_trace.handle(_p('PostToolUse', tool_name='Bash',
                             tool_input={'command': 'ls'}))
    assert r is None
    assert captured_spans == []


def test_exit_plan_mode_response_is_silent_trace(captured_spans, tmp_path):
    """Silent-trace policy (fa3922e): plan_trace posts a span but emits
    suppress_output=True and no additional_context. Without this test,
    a regression that re-enabled the old 'plan_name=X' transcript line
    would be invisible to the test suite."""
    plan_trace.handle(_payload_with_provider(
        'PostToolUse', tmp_path, session_id='s1', tool_name='ExitPlanMode'))
    r = captured_spans[0]  # r is the span, but we need the HookResponse
    # Actually, let's get the response directly
    resp = plan_trace.handle(_payload_with_provider(
        'PostToolUse', tmp_path, session_id='s1', tool_name='ExitPlanMode'))
    assert resp is not None
    assert resp.suppress_output is True
    assert resp.additional_context is None


def test_exit_plan_mode_passes_session_id_to_span(captured_spans, tmp_path):
    """The span's trace_id must equal the payload session_id so downstream
    projections can graft `plan.exit` under the right session."""
    plan_trace.handle(_payload_with_provider(
        'PostToolUse', tmp_path, session_id='session-xyz',
        tool_name='ExitPlanMode'))
    assert captured_spans[0]['trace_id'] == 'session-xyz'


def test_exit_plan_mode_swallows_emit_span_errors(monkeypatch):
    """post_span explosions must not propagate — the handler's
    try/except Exception:pass is explicit about best-effort emission."""
    def _boom(**_kw):
        raise RuntimeError('ingest down')
    import lib.hook_plugin as hp
    monkeypatch.setattr(hp, 'post_span', _boom)

    r = plan_trace.handle(_p('PostToolUse', session_id='s1',
                             tool_name='ExitPlanMode'))
    assert r is not None
    assert r.suppress_output is True


# ── Codex-compatible plan capture ────────────────────────────────────────

class _FakeCodexProvider:
    """Minimal fake for Codex-specific paths."""
    provider_id = 'codex'

    def __init__(self, plans_dir):
        self._plans_dir = plans_dir

    def plans_dir(self):
        return self._plans_dir


def _codex_payload(plans_dir, **kw):
    """Build a PostToolUse payload resolved to the fake Codex provider."""
    payload = _p('PostToolUse', **kw)
    payload.__dict__['resolved_provider'] = _FakeCodexProvider(plans_dir)
    return payload


def test_exit_plan_mode_writes_codex_plan(captured_spans, tmp_path):
    """When the ExitPlanMode payload carries plan text, persist it to the
    provider's plans_dir and tag the span with the written filename."""
    plans_dir = tmp_path / 'codex-plans'
    payload = _codex_payload(
        plans_dir,
        session_id='codex-session-abc',
        tool_name='ExitPlanMode',
        tool_input={'plan': '# My Codex Plan\n\nDo things.'},
    )
    plan_trace.handle(payload)

    written = list(plans_dir.glob('*.md'))
    assert len(written) == 1
    assert written[0].read_text() == '# My Codex Plan\n\nDo things.'

    span = captured_spans[0]
    assert span['name'] == 'plan.exit'
    assert span['attributes']['plan_name'] == written[0].name


def test_exit_plan_mode_camel_case_plan_payload(captured_spans, tmp_path):
    """Codex payloads may use camelCase keys; core.py normalizes them to
    snake_case before the handler sees the payload."""
    plans_dir = tmp_path / 'codex-plans'
    payload = _codex_payload(
        plans_dir,
        session_id='codex-session-def',
        tool_name='ExitPlanMode',
        toolInput={'plan': '# CamelCase Plan\n\nSteps.'},
    )
    plan_trace.handle(payload)

    written = list(plans_dir.glob('*.md'))
    assert len(written) == 1
    assert written[0].read_text() == '# CamelCase Plan\n\nSteps.'
    assert captured_spans[0]['attributes']['plan_name'] == written[0].name


def test_exit_plan_mode_does_not_overwrite_existing_file(captured_spans, tmp_path, monkeypatch):
    """If a deterministic filename already exists, leave it untouched."""
    plans_dir = tmp_path / 'codex-plans'
    plans_dir.mkdir()

    # Pre-create the exact filename that would be generated.
    deterministic_name = 'codex-plan-codexsession-20240101-000000.md'
    existing = plans_dir / deterministic_name
    existing.write_text('original')

    # Monkeypatch datetime so the filename is predictable.
    class _FakeDateTime:
        @classmethod
        def now(cls):
            import datetime
            return datetime.datetime(2024, 1, 1, 0, 0, 0)
        @classmethod
        def strftime(cls, fmt):
            return datetime.datetime(2024, 1, 1, 0, 0, 0).strftime(fmt)

    monkeypatch.setattr(plan_trace, 'datetime', _FakeDateTime)

    payload = _codex_payload(
        plans_dir,
        session_id='codexsession-xyz',
        tool_name='ExitPlanMode',
        tool_input={'plan': '# Overwrite attempt\n'},
    )
    plan_trace.handle(payload)

    assert existing.read_text() == 'original'
    assert captured_spans[0]['attributes']['plan_name'] == deterministic_name


# ── plan.write / plan.update spans + PlanSession enrolment ────────────


def _names(spans: list[dict]) -> list[str]:
    return [s['name'] for s in spans]


def test_exit_plan_with_text_emits_plan_write_span_and_session_row(
        captured_spans, captured_events, tmp_path):
    """When ExitPlanMode carries plan text, the handler writes the file
    AND emits a `plan.write` span tagged with the same plan_filename AND
    POSTs an `enter` event so the session→plan link lands in plan_sessions."""
    plans_dir = tmp_path / 'plans'
    payload = _codex_payload(
        plans_dir,
        session_id='sess-write',
        tool_name='ExitPlanMode',
        tool_input={'plan': '# A new plan\n'},
    )
    plan_trace.handle(payload)

    written = list(plans_dir.glob('*.md'))
    assert len(written) == 1
    plan_filename = written[0].name

    assert _names(captured_spans) == ['plan.exit', 'plan.write']
    write_span = captured_spans[1]
    assert write_span['trace_id'] == 'sess-write'
    assert write_span['attributes']['plan_filename'] == plan_filename
    assert write_span['attributes']['op'] == 'write'
    assert write_span['attributes']['tool_name'] == 'ExitPlanMode'

    assert len(captured_events) == 1
    endpoint, body = captured_events[0]
    assert endpoint == 'plan_sessions'
    assert body['event'] == 'enter'
    assert body['session_id'] == 'sess-write'
    assert body['plan_filename'] == plan_filename
    assert body['started_at']  # ISO timestamp present


def test_exit_plan_without_text_emits_bare_exit_no_attribution(
        captured_spans, captured_events, tmp_path):
    """ExitPlanMode with no plan text (Claude Code) emits a bare `plan.exit`
    boundary marker — no `plan_name`, no `plan.write` span, no PlanSession
    row. We can't prove this session authored any file, and there is no
    mtime fallback that would guess one. Pre-existing plans in the dir
    must not be tagged."""
    plans_dir = tmp_path / 'plans'
    plans_dir.mkdir()
    (plans_dir / 'old.md').write_text('# Pre-existing\n')

    payload = _payload_with_provider(
        'PostToolUse', plans_dir,
        session_id='sess-noclaim',
        tool_name='ExitPlanMode',
        tool_input={},
    )
    plan_trace.handle(payload)

    assert _names(captured_spans) == ['plan.exit']
    assert 'plan_name' not in captured_spans[0]['attributes']
    assert captured_events == []


def test_write_to_plans_dir_emits_plan_write_span_and_session_row(
        captured_spans, captured_events, tmp_path):
    """When the agent invokes `Write` on a file under plans_dir, attribute
    that plan file to this session."""
    plans_dir = tmp_path / 'plans'
    plans_dir.mkdir()
    target = plans_dir / 'fresh.md'

    payload = _payload_with_provider(
        'PostToolUse', plans_dir,
        session_id='sess-W',
        tool_name='Write',
        tool_input={'file_path': str(target), 'content': '# Fresh\n'},
    )
    plan_trace.handle(payload)

    assert _names(captured_spans) == ['plan.write']
    span = captured_spans[0]
    assert span['trace_id'] == 'sess-W'
    assert span['attributes']['plan_filename'] == 'fresh.md'
    assert span['attributes']['op'] == 'write'
    assert span['attributes']['tool_name'] == 'Write'
    assert span['attributes']['file_path'] == str(target)

    assert len(captured_events) == 1
    endpoint, body = captured_events[0]
    assert endpoint == 'plan_sessions'
    assert body['event'] == 'enter'
    assert body['session_id'] == 'sess-W'
    assert body['plan_filename'] == 'fresh.md'


def test_edit_to_plans_dir_emits_plan_update_span(
        captured_spans, captured_events, tmp_path):
    plans_dir = tmp_path / 'plans'
    plans_dir.mkdir()
    target = plans_dir / 'in-progress.md'
    target.write_text('# Existing\n')

    payload = _payload_with_provider(
        'PostToolUse', plans_dir,
        session_id='sess-E',
        tool_name='Edit',
        tool_input={'file_path': str(target),
                    'old_string': 'Existing', 'new_string': 'Updated'},
    )
    plan_trace.handle(payload)

    assert _names(captured_spans) == ['plan.update']
    span = captured_spans[0]
    assert span['attributes']['plan_filename'] == 'in-progress.md'
    assert span['attributes']['op'] == 'update'
    assert span['attributes']['tool_name'] == 'Edit'

    assert len(captured_events) == 1
    assert captured_events[0][1]['session_id'] == 'sess-E'
    assert captured_events[0][1]['plan_filename'] == 'in-progress.md'


def test_multiedit_to_plans_dir_emits_plan_update_span(
        captured_spans, captured_events, tmp_path):
    plans_dir = tmp_path / 'plans'
    plans_dir.mkdir()
    target = plans_dir / 'multi.md'

    payload = _payload_with_provider(
        'PostToolUse', plans_dir,
        session_id='sess-M',
        tool_name='MultiEdit',
        tool_input={'file_path': str(target),
                    'edits': [{'old_string': 'a', 'new_string': 'b'}]},
    )
    plan_trace.handle(payload)

    assert _names(captured_spans) == ['plan.update']
    assert captured_spans[0]['attributes']['tool_name'] == 'MultiEdit'
    assert captured_spans[0]['attributes']['op'] == 'update'


def test_write_outside_plans_dir_is_ignored(
        captured_spans, captured_events, tmp_path):
    """The handler only attributes writes to files *inside* plans_dir."""
    plans_dir = tmp_path / 'plans'
    plans_dir.mkdir()
    other = tmp_path / 'src' / 'main.py'
    other.parent.mkdir()

    payload = _payload_with_provider(
        'PostToolUse', plans_dir,
        session_id='sess-skip',
        tool_name='Write',
        tool_input={'file_path': str(other), 'content': 'x'},
    )
    r = plan_trace.handle(payload)
    assert r is None
    assert captured_spans == []
    assert captured_events == []


def test_write_to_plans_dir_lookalike_path_is_ignored(
        captured_spans, captured_events, tmp_path):
    """`/tmp/plans-extra/foo.md` must not match plans_dir `/tmp/plans`."""
    plans_dir = tmp_path / 'plans'
    plans_dir.mkdir()
    sibling = tmp_path / 'plans-extra'
    sibling.mkdir()
    target = sibling / 'foo.md'

    payload = _payload_with_provider(
        'PostToolUse', plans_dir,
        session_id='sess-lookalike',
        tool_name='Write',
        tool_input={'file_path': str(target), 'content': 'x'},
    )
    plan_trace.handle(payload)
    assert captured_spans == []
    assert captured_events == []


def test_write_to_plans_dir_path_traversal_is_ignored(
        captured_spans, captured_events, tmp_path):
    """A `..`-escaping file_path resolves outside plans_dir and is ignored."""
    plans_dir = tmp_path / 'plans'
    plans_dir.mkdir()
    escape = str(plans_dir / '..' / 'escape.md')

    payload = _payload_with_provider(
        'PostToolUse', plans_dir,
        session_id='sess-trav',
        tool_name='Write',
        tool_input={'file_path': escape, 'content': 'x'},
    )
    plan_trace.handle(payload)
    assert captured_spans == []
    assert captured_events == []


def test_plan_write_without_session_id_skips_event_post(
        captured_spans, captured_events, tmp_path):
    """No session_id → no PlanSession row (the table requires it).
    Span emission can still happen with `trace_id=None`, but the
    endpoint row would be useless without a session to join to."""
    plans_dir = tmp_path / 'plans'
    plans_dir.mkdir()
    target = plans_dir / 'orphan.md'
    payload = _payload_with_provider(
        'PostToolUse', plans_dir,
        session_id='',
        tool_name='Write',
        tool_input={'file_path': str(target), 'content': 'x'},
    )
    plan_trace.handle(payload)
    assert captured_events == []
