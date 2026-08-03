"""The `Stop`/`SessionEnd` subagent sweep must be reachable from the REGISTRY.

`subagent_lifecycle.handle_sweep` self-heals a subagent whose `SubagentStop`
never arrived (Kimi routinely fires none: real session
`session_fb017fec-…` produced 2 `SubagentStart` and 0 `SubagentStop`, leaving
its 86 spans with no `agent_id` and both `tool.Agent` launches PENDING). A unit
test that calls the function directly cannot tell that apart from the function
never being dispatched, so every test here goes through `hook_manager.runner`
with handlers taken from the real `REGISTRY`.

Claude side: `Stop` fires once per assistant turn on every Claude session, so
the sweep must cost a Claude turn nothing. The last two tests measure that —
no reconcile call, and not one extra provider build or DB connection versus
dispatching the same event with no handlers at all.
"""

from __future__ import annotations

import io
import json

import pytest

from hook_manager import runner
from hook_manager.handlers import subagent_lifecycle
from hook_manager.registry import REGISTRY

# Verbatim from ~/.kimi-code/hook-payloads.jsonl (session id swapped for one
# with no wire on disk, so the co-dispatched turn_trace stays a no-op).
_KIMI_STOP = {
    "hook_event_name": "Stop",
    "session_id": "session_sweep-fixture",
    "cwd": "/Users/taowang/regin",
    "stop_hook_active": False,
    "agent_type": "kimi",
}

# Verbatim from ~/.claude/hook-payloads.jsonl. Claude's Stop carries no
# `agent_type`; production supplies it as the `--agent-type` CLI arg, which is
# how `runner.run` receives it below.
_CLAUDE_STOP = {
    "session_id": "354691de-36dd-4721-9a13-29540e9ad42f",
    "cwd": "/Users/taowang/regin",
    "permission_mode": "bypassPermissions",
    "hook_event_name": "Stop",
    "stop_hook_active": False,
    "last_assistant_message": "done",
}


def _sweep_handlers():
    """The registered sweep, looked up the way the runner sees it. Empty (and
    therefore a failing test) if nothing wires `handle_sweep` up.

    Compared against the registry's own binding, not against the raw module
    function: the registry reaches handler modules through a lazy import proxy
    so a hook process only pays for the handlers its event dispatches."""
    from hook_manager import registry
    return [h for h in REGISTRY
            if h.fn is registry.subagent_lifecycle.handle_sweep]


def _dispatch(event, raw, handlers, *, agent_type=None):
    out = io.StringIO()
    code = runner.run(event, handlers, json.dumps(raw), out, agent_type=agent_type)
    return code, out


@pytest.fixture
def _quiet_diagnostics(monkeypatch):
    """Keep the catch-all trace_payload handler from appending to the
    developer's real hook-payloads.jsonl during a full-REGISTRY dispatch."""
    from hook_manager.handlers import trace_payload
    monkeypatch.setattr(trace_payload, '_diagnostics_enabled', lambda: False)


@pytest.fixture
def _reconciled(monkeypatch):
    """Record every session id any provider is asked to reconcile."""
    from lib.providers.base import AgentProvider
    from lib.providers.kimi import KimiProvider
    seen: list[str] = []
    monkeypatch.setattr(AgentProvider, 'reconcile_subagents',
                        lambda self, sid: seen.append(sid))
    monkeypatch.setattr(KimiProvider, 'reconcile_subagents',
                        lambda self, sid: seen.append(sid))
    return seen


# ── registration ───────────────────────────────────────────────────────────

def test_sweep_is_registered_for_stop_and_session_end():
    handlers = _sweep_handlers()
    assert len(handlers) == 1, 'handle_sweep is not wired into REGISTRY'
    handler = handlers[0]
    assert set(handler.events) == {'Stop', 'SessionEnd'}
    assert handler.kind == 'trace'
    assert handler.priority == 60


# ── Kimi: the sweep actually runs ──────────────────────────────────────────

@pytest.mark.parametrize('event', ['Stop', 'SessionEnd'])
def test_kimi_boundary_event_reconciles_through_the_registry(
        _quiet_diagnostics, _reconciled, event):
    """Full-REGISTRY dispatch of a real Kimi payload: reconciliation happens
    without anything calling handle_sweep by hand."""
    raw = dict(_KIMI_STOP, hook_event_name=event)
    code, _ = _dispatch(event, raw, REGISTRY)

    assert code == 0
    assert _reconciled == ['session_sweep-fixture']


def test_kimi_sweep_survives_a_failing_reconciler(monkeypatch, _quiet_diagnostics):
    """A reconciler blowing up must not change the Stop hook's exit code."""
    from lib.providers.kimi import KimiProvider

    def boom(self, _sid):
        raise RuntimeError('reconciler down')

    monkeypatch.setattr(KimiProvider, 'reconcile_subagents', boom)
    code, _ = _dispatch('Stop', _KIMI_STOP, REGISTRY)
    assert code == 0


# ── Claude: the sweep is a no-op, and free ─────────────────────────────────

@pytest.mark.parametrize('event', ['Stop', 'SessionEnd'])
def test_claude_boundary_event_never_reconciles(
        _quiet_diagnostics, _reconciled, event):
    """Claude scopes a subagent's hooks and transcript to the subagent itself,
    and its `reconcile_subagents` re-walks every subagent transcript — running
    it per turn would be a real behaviour change."""
    raw = dict(_CLAUDE_STOP, hook_event_name=event)
    code, _ = _dispatch(event, raw, REGISTRY, agent_type='claude')

    assert code == 0
    assert _reconciled == []


def test_claude_stop_costs_nothing_extra(monkeypatch):
    """Measured Claude cost of adding the sweep: zero provider builds, zero DB
    connections. Dispatching the same Stop with the sweep and with no handlers
    at all must produce identical counts."""
    import lib.orm.engine as orm_engine
    import lib.providers.registry as provider_registry

    counts = {'build_provider': 0, 'get_connection': 0}
    real_build = provider_registry.build_provider
    real_connect = orm_engine.get_connection

    def counting_build(provider_id):
        counts['build_provider'] += 1
        return real_build(provider_id)

    def counting_connect():
        counts['get_connection'] += 1
        return real_connect()

    monkeypatch.setattr(provider_registry, 'build_provider', counting_build)
    monkeypatch.setattr(orm_engine, 'get_connection', counting_connect)

    _dispatch('Stop', _CLAUDE_STOP, [], agent_type='claude')
    baseline = dict(counts)

    counts.update(build_provider=0, get_connection=0)
    _dispatch('Stop', _CLAUDE_STOP, _sweep_handlers(), agent_type='claude')

    assert counts == baseline
    assert counts['get_connection'] == 0
