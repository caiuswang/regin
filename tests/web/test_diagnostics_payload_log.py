"""The payload-log browser must be switchable by agent (provider).

Each provider writes its hook payloads to its own JSONL (Claude →
~/.claude, Kimi → ~/.kimi-code, …). The Diagnostics → Payload log page
mirrors Schema drift: it lists every provider with a log in `agents` and
reads the one named by `?agent=`, falling back to the active provider.
"""

from __future__ import annotations

import json
from pathlib import Path

from web.blueprints import diagnostics


class _FakeProvider:
    def __init__(self, provider_id: str, log_path: Path):
        self.provider_id = provider_id
        self._log_path = log_path

    def hook_payload_log_path(self) -> Path:
        return self._log_path


def _write_entries(path: Path, entries: list[dict]) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


def _entry(tool: str) -> dict:
    return {
        "received_at": "2026-06-17T10:00:00",
        "hook_event": "PostToolUse",
        "session_id": "s1",
        "payload": {"tool_name": tool, "tool_input": {}},
    }


def _patch_providers(monkeypatch, by_agent: dict[str, _FakeProvider], active: str):
    monkeypatch.setattr(diagnostics, "is_provider_id", lambda a: a in by_agent)
    monkeypatch.setattr(diagnostics, "build_provider", lambda a: by_agent[a])
    monkeypatch.setattr(diagnostics, "list_provider_ids", lambda: list(by_agent))
    monkeypatch.setattr(
        diagnostics, "get_active_provider", lambda: by_agent[active],
    )


def test_lists_agents_and_defaults_to_active(flask_client, tmp_path, monkeypatch):
    claude_log = tmp_path / "claude.jsonl"
    kimi_log = tmp_path / "kimi.jsonl"
    _write_entries(claude_log, [_entry("Bash")])
    _write_entries(kimi_log, [_entry("Read"), _entry("Edit")])
    _patch_providers(
        monkeypatch,
        by_agent={
            "claude": _FakeProvider("claude", claude_log),
            "kimi": _FakeProvider("kimi", kimi_log),
        },
        active="claude",
    )

    resp = flask_client.get("/api/diagnostics/payload-log")
    body = resp.get_json()

    assert resp.status_code == 200
    # Both providers' logs exist → both are switchable tabs.
    assert body["agents"] == ["claude", "kimi"]
    # No ?agent → active provider's log, and the response says which.
    assert body["agent"] == "claude"
    assert [e["tool_name"] for e in body["entries"]] == ["Bash"]


def test_reads_the_requested_agents_log(flask_client, tmp_path, monkeypatch):
    claude_log = tmp_path / "claude.jsonl"
    kimi_log = tmp_path / "kimi.jsonl"
    _write_entries(claude_log, [_entry("Bash")])
    _write_entries(kimi_log, [_entry("Read"), _entry("Edit")])
    _patch_providers(
        monkeypatch,
        by_agent={
            "claude": _FakeProvider("claude", claude_log),
            "kimi": _FakeProvider("kimi", kimi_log),
        },
        active="claude",
    )

    resp = flask_client.get("/api/diagnostics/payload-log?agent=kimi")
    body = resp.get_json()

    assert body["agent"] == "kimi"
    assert [e["tool_name"] for e in body["entries"]] == ["Read", "Edit"]


def test_unknown_agent_falls_back_to_active(flask_client, tmp_path, monkeypatch):
    claude_log = tmp_path / "claude.jsonl"
    _write_entries(claude_log, [_entry("Bash")])
    _patch_providers(
        monkeypatch,
        by_agent={"claude": _FakeProvider("claude", claude_log)},
        active="claude",
    )

    resp = flask_client.get("/api/diagnostics/payload-log?agent=bogus")
    body = resp.get_json()

    assert body["agent"] == "claude"
    assert [e["tool_name"] for e in body["entries"]] == ["Bash"]
