"""`_lookup_payload` must read the drift row's *agent's* payload log.

Each provider writes hook payloads to its own JSONL (Claude → ~/.claude,
Kimi → ~/.kimi-code). A kimi drift finding investigated in the WebUI
while Claude is the active provider must still resolve its payload from
kimi's log — not the active provider's. Regression test for that routing.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.trace.payload_drift_store import _sha256
from web.blueprints import schema_drift


def _write_entry(path: Path, payload: dict) -> None:
    path.write_text(json.dumps({"payload": payload}) + "\n")


class _FakeProvider:
    def __init__(self, log_path: Path):
        self._log_path = log_path

    def hook_payload_log_path(self) -> Path:
        return self._log_path


def _patch_providers(monkeypatch, by_agent: dict[str, Path], active: Path):
    monkeypatch.setattr(schema_drift, "is_provider_id", lambda a: a in by_agent)
    monkeypatch.setattr(
        schema_drift, "build_provider",
        lambda a: _FakeProvider(by_agent[a]),
    )
    monkeypatch.setattr(
        schema_drift, "get_active_provider",
        lambda: _FakeProvider(active),
    )


def test_lookup_reads_the_drift_agents_log(tmp_path, monkeypatch):
    claude_log = tmp_path / "claude.jsonl"
    kimi_log = tmp_path / "kimi.jsonl"
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_input": {"command": "ls"}}
    _write_entry(kimi_log, payload)          # only kimi's log has it
    claude_log.write_text("")                # claude's log is empty

    # Active provider is Claude, but the finding belongs to kimi.
    _patch_providers(
        monkeypatch,
        by_agent={"claude": claude_log, "kimi": kimi_log},
        active=claude_log,
    )

    sha = _sha256(payload)
    assert schema_drift._lookup_payload(sha, agent="kimi") == payload
    # Without agent routing this would read Claude's (empty) log and miss.
    assert schema_drift._lookup_payload(sha, agent="claude") is None


def test_lookup_falls_back_to_active_provider(tmp_path, monkeypatch):
    """Unknown / missing agent → active provider's log (back-compat)."""
    active_log = tmp_path / "active.jsonl"
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Read",
               "tool_input": {"file_path": "/x"}}
    _write_entry(active_log, payload)
    _patch_providers(monkeypatch, by_agent={}, active=active_log)

    sha = _sha256(payload)
    assert schema_drift._lookup_payload(sha, agent=None) == payload
    assert schema_drift._lookup_payload(sha, agent="kimi") == payload
