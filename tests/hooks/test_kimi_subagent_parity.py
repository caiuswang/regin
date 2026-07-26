"""Provider parity for the subagent lifecycle hooks.

Two Kimi-specific defects, both of which must stay invisible to Claude:

* Kimi puts its PROVIDER id in `agent_type` ('kimi') and the real subagent kind
  in `agent_name` ('explore'), so a verbatim copy labelled every Kimi subagent
  card with the provider name.
* Reconciliation used to hang off `SubagentStop` alone. Kimi frequently never
  fires one, so a missed Stop was permanent; it now also runs at
  `SubagentStart` and on the `Stop`/`SessionEnd` sweep.
"""

from __future__ import annotations

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import subagent_lifecycle


def _p(event, **kw):
    return HookPayload.from_stdin_json(event, {'hook_event_name': event, **kw})


@pytest.fixture
def captured_spans(monkeypatch):
    import lib.hook_plugin as hp
    spans: list[dict] = []
    monkeypatch.setattr(hp, 'post_span', lambda **kw: spans.append(kw))
    return spans


@pytest.fixture
def reconciled(monkeypatch):
    """Record every trace_id the Kimi provider is asked to reconcile."""
    from lib.providers.kimi import KimiProvider
    seen: list[str] = []
    monkeypatch.setattr(KimiProvider, 'reconcile_subagents',
                        lambda self, sid: seen.append(sid))
    return seen


# ── the subagent's kind, not the provider's ────────────────────────────────

def test_kimi_marker_uses_agent_name_not_provider_id(captured_spans):
    subagent_lifecycle.handle_start(_p(
        'SubagentStart', session_id='session_k', agent_type='kimi',
        agent_name='explore', prompt='Explore the repo'))
    attrs = captured_spans[0]['attributes']
    assert attrs['agent_type'] == 'explore'
    assert attrs['agent_name'] == 'explore'


def test_claude_marker_keeps_its_agent_type(captured_spans):
    """Claude sends the subagent's own type slug in `agent_type` and no
    `agent_name` — the branch above must never fire for it."""
    subagent_lifecycle.handle_start(_p(
        'SubagentStart', session_id='s1', agent_type='Explore',
        agent_id='abcdef1234567890'))
    attrs = captured_spans[0]['attributes']
    assert attrs['agent_type'] == 'Explore'
    assert attrs['agent_id'] == 'abcdef1234567890'


def test_claude_empty_agent_type_still_omitted(captured_spans):
    """Claude's real SubagentStop payloads carry `agent_type: ''`; the key must
    stay absent rather than becoming an empty string."""
    subagent_lifecycle.handle_start(_p(
        'SubagentStart', session_id='s1', agent_type='', agent_id='a1'))
    assert 'agent_type' not in captured_spans[0]['attributes']


def test_provider_named_subagent_survives_without_a_name(captured_spans):
    """A subagent genuinely typed after a provider keeps that type when there
    is no distinct `agent_name` to prefer."""
    subagent_lifecycle.handle_start(_p(
        'SubagentStart', session_id='s1', agent_type='claude'))
    assert captured_spans[0]['attributes']['agent_type'] == 'claude'


# ── reconciliation trigger points ──────────────────────────────────────────

def test_start_triggers_reconcile(captured_spans, reconciled):
    subagent_lifecycle.handle_start(_p(
        'SubagentStart', session_id='session_k', agent_type='kimi',
        agent_name='explore'))
    assert reconciled == ['session_k']


@pytest.mark.parametrize('event', ['Stop', 'SessionEnd'])
def test_sweep_triggers_reconcile(reconciled, event):
    subagent_lifecycle.handle_sweep(_p(event, session_id='session_k',
                                       agent_type='kimi'))
    assert reconciled == ['session_k']


@pytest.mark.parametrize('provider_id', ['claude', 'codex'])
def test_sweep_and_start_never_reconcile_a_claude_shaped_session(
        monkeypatch, captured_spans, provider_id):
    """Claude's `reconcile_subagents` re-walks every subagent transcript, so
    firing it per-turn would be a real behaviour change. Only providers whose
    sub-tool hooks land on the PARENT session (transcript_format != 'claude')
    take the new trigger points; SubagentStop is untouched for everyone."""
    from lib.providers.base import AgentProvider
    calls: list[str] = []
    monkeypatch.setattr(AgentProvider, 'reconcile_subagents',
                        lambda self, sid: calls.append(sid), raising=False)
    subagent_lifecycle.handle_sweep(_p('Stop', session_id='s1',
                                       agent_type=provider_id))
    subagent_lifecycle.handle_start(_p('SubagentStart', session_id='s1',
                                       agent_type=provider_id))
    assert calls == []


def test_claude_subagent_stop_still_reconciles(monkeypatch, captured_spans):
    """The pre-existing SubagentStop trigger must keep firing for Claude."""
    from lib.providers.claude import ClaudeProvider
    calls: list[str] = []
    monkeypatch.setattr(ClaudeProvider, 'reconcile_subagents',
                        lambda self, sid: calls.append(sid))
    subagent_lifecycle.handle_stop(_p(
        'SubagentStop', session_id='s1', agent_type='Explore',
        agent_id='a1', last_assistant_message='done'))
    assert calls == ['s1']


def test_sweep_swallows_provider_errors(monkeypatch):
    """A failing reconcile must never break the Stop hook."""
    from lib.providers.kimi import KimiProvider

    def boom(self, _sid):
        raise RuntimeError('reconciler down')

    monkeypatch.setattr(KimiProvider, 'reconcile_subagents', boom)
    resp = subagent_lifecycle.handle_sweep(_p('Stop', session_id='session_k',
                                              agent_type='kimi'))
    assert resp.suppress_output is True
