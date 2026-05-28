"""Tests for the skill_launch hook handler.

Covers the third skill-trace channel: assistant-initiated Skill tool calls
(distinct from content.md reads and slash-command invocations). Pins the
HookResponse shape and the `source='launch'` ingest payload.
"""

from __future__ import annotations

from hook_manager.core import HookPayload
from hook_manager.handlers import skill_launch


def _payload(**overrides) -> HookPayload:
    base = dict(
        event='PostToolUse',
        session_id='trace-abc',
        tool_name='Skill',
        tool_input={'skill': 'frontend-style-convention'},
        raw={},
    )
    base.update(overrides)
    return HookPayload(**base)


def _capture_post_event(monkeypatch):
    captured: list[tuple[str, dict]] = []

    def _stub_span(**_kwargs):
        return None

    def _stub_event(endpoint, data, agent_type=None):
        captured.append((endpoint, data))
        return None

    monkeypatch.setattr('lib.hook_plugin.post_span', _stub_span)
    monkeypatch.setattr('lib.hook_plugin.post_event', _stub_event)
    return captured


def test_returns_none_for_non_skill_tool():
    response = skill_launch.handle(_payload(tool_name='Read', tool_input={'file_path': '/tmp/x'}))
    assert response is None


def test_returns_none_when_skill_id_missing(monkeypatch):
    captured = _capture_post_event(monkeypatch)
    response = skill_launch.handle(_payload(tool_input={}))
    assert response is None
    assert captured == []


def test_emits_launch_event_with_source(monkeypatch):
    captured = _capture_post_event(monkeypatch)
    response = skill_launch.handle(_payload())

    assert response is not None
    assert response.suppress_output is True
    assert 'frontend-style-convention' in (response.additional_context or '')

    assert len(captured) == 1
    endpoint, data = captured[0]
    assert endpoint == 'skill_reads'
    assert data['skill_id'] == 'frontend-style-convention'
    assert data['session_id'] == 'trace-abc'
    assert data['source'] == 'launch'
    assert data['file_path'] == '.claude/skills/frontend-style-convention/launch'
    assert data['found'] is True


def test_handler_swallows_emit_exceptions(monkeypatch):
    def _boom(*_a, **_kw):
        raise RuntimeError('ingest down')
    monkeypatch.setattr('lib.hook_plugin.post_span', _boom)
    monkeypatch.setattr('lib.hook_plugin.post_event', _boom)

    response = skill_launch.handle(_payload())
    assert response is not None
    assert response.suppress_output is True
