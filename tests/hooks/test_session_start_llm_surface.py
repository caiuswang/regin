"""SessionStart hook: `llm_surface` tagging from REGIN_LLM_SURFACE.

`ExternalAgentLLM` tags its spawned agent's env with the surface id; the
SessionStart hook (running inside that subprocess tree) copies it onto the
`session.start` span so ingest can stamp the session `origin='llm-stage'`.
"""

from __future__ import annotations

from hook_manager.core import HookPayload
from hook_manager.handlers import session_lifecycle


def _payload():
    raw = {"cwd": None, "session_id": "s-llm", "source": "startup",
           "agent_type": "claude"}
    return HookPayload(event="SessionStart", cwd=None,
                       session_id="s-llm", raw=raw)


def _capture_spans(monkeypatch):
    posted = []

    def _fake_post_span(*, trace_id, name, attributes=None, **_kw):
        posted.append({"trace_id": trace_id, "name": name,
                       "attributes": attributes or {}})
        return True

    from lib import hook_plugin
    monkeypatch.setattr(hook_plugin, "post_span", _fake_post_span)
    return posted


def _session_starts(posted):
    return [s for s in posted if s["name"] == "session.start"]


def test_session_start_attaches_llm_surface_from_env(monkeypatch):
    posted = _capture_spans(monkeypatch)
    monkeypatch.setenv("REGIN_LLM_SURFACE", "memory-reflect-contradiction")
    session_lifecycle.handle_start(_payload())
    starts = _session_starts(posted)
    assert starts
    assert starts[0]["attributes"]["llm_surface"] == "memory-reflect-contradiction"


def test_session_start_omits_llm_surface_without_env(monkeypatch):
    posted = _capture_spans(monkeypatch)
    monkeypatch.delenv("REGIN_LLM_SURFACE", raising=False)
    session_lifecycle.handle_start(_payload())
    starts = _session_starts(posted)
    assert starts
    assert "llm_surface" not in starts[0]["attributes"]
