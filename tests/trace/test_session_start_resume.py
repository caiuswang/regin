"""F181 A3: SessionStart resume/fork fields reach session.start and `stats session`."""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from cli import output
from cli.app import app
from hook_manager.core import HookPayload
from hook_manager.handlers import session_lifecycle
from lib.orm import SessionLocal
from lib.orm.models import SessionSpan
from lib.orm.models.trace import Session as SessionModel
from lib.trace.payload_validation import validate_event

RESUME_PAYLOAD = {
    'hook_event_name': 'SessionStart',
    'session_id': 'resume-a3',
    'cwd': '/Users/taowang/regin',
    'source': 'resume',
    'seconds_since_last_response': 4000,
    'context_tokens': 180000,
    'prompt_cache_likely_expired': True,
    'estimated_cache_write_usd': 1.2,
}


@pytest.fixture
def captured_spans(monkeypatch):
    import lib.hook_plugin as hp
    spans: list[dict] = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    monkeypatch.setattr(session_lifecycle, '_emit_git_status', lambda p: None)
    return spans


def _start(captured, **payload) -> dict:
    session_lifecycle.handle_start(
        HookPayload.from_stdin_json('SessionStart', payload))
    starts = [s for s in captured if s['name'] == 'session.start']
    assert len(starts) == 1
    return starts[0]


def test_session_start_resume_fields_recorded(captured_spans):
    attrs = _start(captured_spans, **RESUME_PAYLOAD)['attributes']

    assert attrs['source'] == 'resume'
    assert attrs['seconds_since_last_response'] == 4000
    assert attrs['context_tokens'] == 180000
    assert attrs['prompt_cache_likely_expired'] is True
    assert attrs['estimated_cache_write_usd'] == 1.2
    unknown = [f.field_path for f in validate_event('SessionStart', RESUME_PAYLOAD)
               if f.drift_kind == 'unknown_field']
    assert unknown == []


def test_session_start_records_a_warm_resume_flag(captured_spans):
    attrs = _start(captured_spans, **{**RESUME_PAYLOAD,
                                      'prompt_cache_likely_expired': False,
                                      'seconds_since_last_response': 0})['attributes']

    assert attrs['prompt_cache_likely_expired'] is False
    assert attrs['seconds_since_last_response'] == 0


def test_session_start_fork_source_recorded(captured_spans):
    attrs = _start(captured_spans, hook_event_name='SessionStart',
                   session_id='fork-a3', source='fork')['attributes']

    assert attrs['source'] == 'fork'


BASE = datetime(2026, 10, 20, 9, 0, 0)


def _seed(trace_id: str, start_attrs: dict) -> None:
    ts = lambda s: (BASE + timedelta(seconds=s)).isoformat()  # noqa: E731
    events = [(0, 'session.start', start_attrs), (5, 'prompt', {'text': 'go on'}),
              (40, 'tool.Edit', {'file_path': '/a.py'})]
    with SessionLocal() as s:
        s.add(SessionModel(
            trace_id=trace_id, title='resumed', started_at=ts(0),
            last_seen=ts(40), cwd='/Users/taowang/regin', model='claude-opus-5',
            agent_type='claude', origin='session', is_test=0, prompts=1,
            tool_calls=1, cost_usd=3.0, input_tokens=10, output_tokens=20))
        for i, (off, name, attrs) in enumerate(events):
            s.add(SessionSpan(
                trace_id=trace_id, span_id=f'{trace_id}-{i}', parent_id=None,
                name=name, kind='internal', start_time=ts(off),
                attributes=json.dumps(attrs), status_code='OK'))
        s.commit()


@pytest.fixture
def cli_out(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr(output, '_stdout', buf)
    return buf


def test_stats_session_explains_expensive_resume(captured_spans, cli_out):
    _seed('resume-a3', _start(captured_spans, **RESUME_PAYLOAD)['attributes'])

    result = CliRunner().invoke(app, ['stats', 'session', 'resume-a3',
                                      '--format', 'json'])
    assert result.exit_code == 0, result.output
    payload = json.loads(cli_out.getvalue())
    [finding] = [f for f in payload['findings']
                 if f['kind'] == 'resume-cache-rewrite']
    assert finding['usd'] == pytest.approx(1.2)
    assert '$1.20' in finding['headline']
    assert any('seconds_since_last_response=4000' in d for d in finding['detail'])
    assert payload['attributed_ms'] + payload['remainder_ms'] == payload['attended_ms']

    cli_out.truncate(0)
    cli_out.seek(0)
    CliRunner().invoke(app, ['stats', 'session', 'resume-a3'])
    assert '$ 1.20  Resume re-wrote an expired prompt cache' in cli_out.getvalue()


def test_non_numeric_cache_estimate_does_not_break_diagnosis(captured_spans, cli_out):
    attrs = _start(captured_spans, **{**RESUME_PAYLOAD,
                                      'estimated_cache_write_usd': '1.2'})['attributes']
    _seed('resume-str', attrs)

    result = CliRunner().invoke(app, ['stats', 'session', 'resume-str', '--format', 'json'])
    assert result.exit_code == 0, result.output
    kinds = [f['kind'] for f in json.loads(cli_out.getvalue())['findings']]
    assert 'resume-cache-rewrite' not in kinds


def test_warm_resume_raises_no_cache_finding(captured_spans, cli_out):
    attrs = _start(captured_spans, **{**RESUME_PAYLOAD,
                                      'prompt_cache_likely_expired': False})['attributes']
    _seed('resume-warm', attrs)

    CliRunner().invoke(app, ['stats', 'session', 'resume-warm', '--format', 'json'])
    kinds = [f['kind'] for f in json.loads(cli_out.getvalue())['findings']]
    assert 'resume-cache-rewrite' not in kinds
