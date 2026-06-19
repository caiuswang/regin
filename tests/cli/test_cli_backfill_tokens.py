"""Characterization tests for `regin trace backfill-tokens`.

Pins the observable behavior of `cmd_backfill_tokens` before a
complexity refactor: the printed summary counters and the payload
handed to `ingest_turn_usage`. All external collaborators
(`get_connection`, `_find_transcript`, `provider.parse_transcript`,
`_richest_model`, `ingest_turn_usage`) are monkeypatched so the test
exercises only the command's own control flow.

Transcript parsing now routes through the active provider's
`parse_transcript` (so non-Claude on-disk formats like Kimi's
wire.jsonl parse correctly), so the fake provider exposes that method.
"""

from __future__ import annotations

import types

import pytest
import typer

import cli.commands.trace as trace_cmd


class _FakeRow(dict):
    """Row that supports `r['key']` access like sqlite3.Row."""


class _FakeConn:
    """Minimal stand-in for an sqlite3 connection.

    `sessions_rows` are returned by the candidate `SELECT`; any other
    `execute` (the per-session `UPDATE`) is captured in `updates`.
    """

    def __init__(self, sessions_rows, updates):
        self._sessions_rows = sessions_rows
        self._updates = updates
        self.commits = 0
        self.closed = False

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("SELECT"):
            return types.SimpleNamespace(fetchall=lambda: self._sessions_rows)
        # UPDATE sessions SET model ...
        self._updates.append((sql, params))
        return types.SimpleNamespace(fetchall=lambda: [])

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class _FakeTurn:
    def __init__(self, uuid, timestamp, *, model=None, idx=0):
        self.uuid = uuid
        self.timestamp = timestamp
        self.model = model
        self.input_tokens = 10 + idx
        self.output_tokens = 20 + idx
        self.cache_read_tokens = 30 + idx
        self.cache_creation_tokens = 40 + idx
        self.context_used = 50 + idx
        self.request_id = f"req-{idx}"


class _FakeUsage:
    def __init__(self, turns, model="usage-model"):
        self.turns = turns
        self.model = model


def _install(monkeypatch, *, sessions_rows, transcripts, usages,
             richest_model="rich-model"):
    """Wire up all collaborators. Returns a dict capturing side effects."""
    captured = {"ingested": [], "updates": []}

    provider = types.SimpleNamespace(
        capabilities=types.SimpleNamespace(transcript_usage=True),
        display_name="FakeProvider",
        parse_transcript=lambda path: usages.get(path),
    )
    monkeypatch.setattr(trace_cmd, "get_active_provider", lambda: provider)

    conn = _FakeConn(sessions_rows, captured["updates"])
    import lib.orm.engine as engine_mod
    monkeypatch.setattr(engine_mod, "get_connection", lambda: conn)

    monkeypatch.setattr(
        trace_cmd, "_find_transcript",
        lambda tid: transcripts.get(tid),
    )

    monkeypatch.setattr(
        trace_cmd, "_richest_model",
        lambda c, tid, fallbacks: richest_model,
    )

    import lib.trace.trace_service as ts_mod
    monkeypatch.setattr(
        ts_mod, "ingest_turn_usage",
        lambda payload: captured["ingested"].append(payload),
    )
    return captured


def test_unsupported_provider_exits_2(monkeypatch):
    provider = types.SimpleNamespace(
        capabilities=types.SimpleNamespace(transcript_usage=False),
        display_name="Codex",
    )
    monkeypatch.setattr(trace_cmd, "get_active_provider", lambda: provider)
    with pytest.raises(typer.Exit) as exc:
        trace_cmd.cmd_backfill_tokens(only_missing=True, limit=0)
    assert exc.value.exit_code == 2


def test_happy_path_one_session(monkeypatch, capsys):
    rows = [_FakeRow(trace_id="t1", model="old-model")]
    turns = [_FakeTurn("u0", "2026-01-01T00:00:00Z", model="turn-model", idx=0)]
    captured = _install(
        monkeypatch,
        sessions_rows=rows,
        transcripts={"t1": "/path/t1.jsonl"},
        usages={"/path/t1.jsonl": _FakeUsage(turns)},
        richest_model="rich-model",
    )
    trace_cmd.cmd_backfill_tokens(only_missing=True, limit=0)
    out = capsys.readouterr().out
    assert "Found 1 candidate sessions." in out
    assert "Done. updated=1 missing_transcript=0 empty_usage=0" in out
    assert len(captured["ingested"]) == 1
    payload = captured["ingested"][0]
    assert payload == [{
        'trace_id': "t1",
        'turn_uuid': "u0",
        'turn_index': 0,
        'timestamp': "2026-01-01T00:00:00Z",
        'model': "turn-model",
        'input_tokens': 10,
        'output_tokens': 20,
        'cache_read_tokens': 30,
        'cache_creation_tokens': 40,
        'context_used_tokens': 50,
        'request_id': "req-0",
    }]
    # model differs from current -> UPDATE sessions fired
    assert len(captured["updates"]) == 1


def test_turn_without_model_falls_back_to_richest(monkeypatch, capsys):
    rows = [_FakeRow(trace_id="t1", model="old-model")]
    turns = [_FakeTurn("u0", "ts", model=None, idx=0)]
    captured = _install(
        monkeypatch,
        sessions_rows=rows,
        transcripts={"t1": "/p.jsonl"},
        usages={"/p.jsonl": _FakeUsage(turns)},
        richest_model="rich-model",
    )
    trace_cmd.cmd_backfill_tokens(only_missing=False, limit=0)
    payload = captured["ingested"][0]
    assert payload[0]["model"] == "rich-model"


def test_missing_transcript_counted(monkeypatch, capsys):
    rows = [_FakeRow(trace_id="t1", model="m")]
    captured = _install(
        monkeypatch,
        sessions_rows=rows,
        transcripts={},  # no transcript for t1
        usages={},
    )
    trace_cmd.cmd_backfill_tokens(only_missing=True, limit=0)
    out = capsys.readouterr().out
    assert "Done. updated=0 missing_transcript=1 empty_usage=0" in out
    assert captured["ingested"] == []


def test_empty_usage_counted(monkeypatch, capsys):
    rows = [_FakeRow(trace_id="t1", model="m")]
    captured = _install(
        monkeypatch,
        sessions_rows=rows,
        transcripts={"t1": "/p.jsonl"},
        usages={"/p.jsonl": _FakeUsage([])},  # no turns
    )
    trace_cmd.cmd_backfill_tokens(only_missing=True, limit=0)
    out = capsys.readouterr().out
    assert "Done. updated=0 missing_transcript=0 empty_usage=1" in out
    assert captured["ingested"] == []


def test_empty_payload_is_silently_dropped(monkeypatch, capsys):
    """All turns lack uuid/timestamp -> payload empty.

    Original behavior: neither `updated` nor `empty` is incremented,
    BUT the model UPDATE still fires (richest != current).
    """
    rows = [_FakeRow(trace_id="t1", model="old-model")]
    # turns present (so not "empty_usage") but each missing uuid/timestamp
    turns = [_FakeTurn("", "", idx=0), _FakeTurn("u1", "", idx=1)]
    captured = _install(
        monkeypatch,
        sessions_rows=rows,
        transcripts={"t1": "/p.jsonl"},
        usages={"/p.jsonl": _FakeUsage(turns)},
        richest_model="rich-model",
    )
    trace_cmd.cmd_backfill_tokens(only_missing=True, limit=0)
    out = capsys.readouterr().out
    assert "Done. updated=0 missing_transcript=0 empty_usage=0" in out
    assert captured["ingested"] == []
    # model resolution still persisted despite empty payload
    assert len(captured["updates"]) == 1


def test_limit_caps_updated_sessions(monkeypatch, capsys):
    rows = [
        _FakeRow(trace_id="t1", model="m"),
        _FakeRow(trace_id="t2", model="m"),
        _FakeRow(trace_id="t3", model="m"),
    ]
    usage = _FakeUsage([_FakeTurn("u", "ts", idx=0)])
    captured = _install(
        monkeypatch,
        sessions_rows=rows,
        transcripts={"t1": "/a", "t2": "/b", "t3": "/c"},
        usages={"/a": usage, "/b": usage, "/c": usage},
        richest_model="m",  # equals current -> no UPDATE noise
    )
    trace_cmd.cmd_backfill_tokens(only_missing=True, limit=2)
    out = capsys.readouterr().out
    assert "Done. updated=2 missing_transcript=0 empty_usage=0" in out
    assert len(captured["ingested"]) == 2


def test_richest_equals_current_no_update(monkeypatch, capsys):
    rows = [_FakeRow(trace_id="t1", model="same-model")]
    captured = _install(
        monkeypatch,
        sessions_rows=rows,
        transcripts={"t1": "/p.jsonl"},
        usages={"/p.jsonl": _FakeUsage([_FakeTurn("u", "ts", idx=0)])},
        richest_model="same-model",
    )
    trace_cmd.cmd_backfill_tokens(only_missing=True, limit=0)
    assert captured["updates"] == []
