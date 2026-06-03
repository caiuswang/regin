"""Unit tests for web.blueprints.trace endpoints not covered by
tests/test_trace_api.py.

Covers /api/skill-reads/reset, /api/sessions/<id>/spans/<id>/children,
/api/sessions/batch-delete, DELETE /api/sessions/<id>, /api/ingest-errors.
"""

from __future__ import annotations

import json

from sqlmodel import select

from lib.orm import SessionLocal
from lib.orm.models import (
    PlanSession, RuleTrigger, Session as SessionModel, SessionRepo,
    SessionSpan, SessionTraceMap, SkillRead, TurnUsage,
)


def _seed_skill_reads(count: int = 3):
    with SessionLocal() as session:
        for i in range(count):
            session.add(SkillRead(
                skill_id=f"skill-{i}",
                session_id="s1",
                file_path=f"~/.claude/skills/skill-{i}/SKILL.md",
                read_at=f"2026-04-22 10:0{i}:00",
            ))
        session.commit()


def _seed_session(trace_id: str):
    with SessionLocal() as session:
        session.add(SessionModel(
            trace_id=trace_id, title="Session",
            started_at="2026-04-22 10:00:00",
            last_seen="2026-04-22 10:00:00",
        ))
        session.add(SessionSpan(
            trace_id=trace_id, span_id="root-span", parent_id=None,
            name="root", kind="internal",
            start_time="2026-04-22 10:00:00",
        ))
        session.add(SkillRead(
            skill_id="x", session_id=trace_id,
            file_path="~/.claude/skills/x/SKILL.md",
            read_at="2026-04-22 10:01:00",
        ))
        session.add(PlanSession(
            session_id=trace_id, plan_filename="p.md",
            started_at="2026-04-22 10:00:00",
        ))
        session.add(RuleTrigger(
            rule_id="r", file_path="f.java", match_count=0, triggered=0,
            session_id=trace_id, checked_at="2026-04-22 10:00:00",
        ))
        session.add(SessionTraceMap(
            trace_id=trace_id, span_id="root-span", parent_id=None,
            name="root", start_time="2026-04-22 10:00:00",
        ))
        session.add(TurnUsage(
            trace_id=trace_id, turn_uuid="t1", turn_index=0,
            timestamp="2026-04-22 10:00:00", input_tokens=1, output_tokens=1,
            cache_read_tokens=0, cache_creation_tokens=0, context_used_tokens=1,
        ))
        session.add(SessionRepo(trace_id=trace_id, repo_id=1, is_primary=1))
        session.commit()


def _assert_no_session_residue(trace_id: str):
    """Fail if any session-keyed table still holds a row for `trace_id`.

    Drives off the production target list so a newly-added table that the
    delete path forgets to clear is caught here automatically.
    """
    from web.blueprints.trace.sessions import _SESSION_DELETE_TARGETS
    with SessionLocal() as session:
        for key, model, column in _SESSION_DELETE_TARGETS:
            remaining = session.exec(
                select(model).where(column == trace_id)
            ).all()
            assert remaining == [], f"{key} left {len(remaining)} orphan row(s)"


# ── POST /api/skill-reads/reset ─────────────────────────────

def test_reset_skill_reads_deletes_all(flask_client, tmp_db):
    _seed_skill_reads(3)
    resp = flask_client.post("/api/skill-reads/reset", json={})
    body = resp.get_json()
    assert body["ok"] is True
    assert "3 row" in body["msg"]

    with SessionLocal() as session:
        assert session.exec(select(SkillRead)).all() == []


def test_reset_skill_reads_filter_by_skill(flask_client, tmp_db):
    _seed_skill_reads(2)
    # Only drop skill-0.
    resp = flask_client.post("/api/skill-reads/reset",
                               json={"skill": "skill-0"})
    assert resp.get_json()["ok"] is True
    with SessionLocal() as session:
        remaining = session.exec(select(SkillRead)).all()
        assert {r.skill_id for r in remaining} == {"skill-1"}


def test_reset_skill_reads_filter_by_session(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(SkillRead(skill_id="a", session_id="keep",
                                file_path="p",
                                read_at="2026-04-22 10:00:00"))
        session.add(SkillRead(skill_id="b", session_id="drop",
                                file_path="p",
                                read_at="2026-04-22 10:01:00"))
        session.commit()
    resp = flask_client.post("/api/skill-reads/reset",
                               json={"session": "drop"})
    assert resp.get_json()["ok"] is True
    with SessionLocal() as session:
        remaining = session.exec(select(SkillRead)).all()
        assert {r.session_id for r in remaining} == {"keep"}


# ── GET /api/sessions/<id>/spans/<span>/children ────────────

def test_span_children_unknown_span_returns_empty(flask_client, tmp_db):
    _seed_session("trace-a")
    resp = flask_client.get(
        "/api/sessions/trace-a/spans/unknown-span/children",
    )
    body = resp.get_json()
    assert body["trace_id"] == "trace-a"
    assert body["parent_span_id"] == "unknown-span"
    assert body["children"] == []
    assert body["spans"] == []


def test_span_children_empty_session(flask_client, tmp_db):
    resp = flask_client.get(
        "/api/sessions/nonexistent/spans/x/children",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["children"] == []


def test_session_map_shallow_returns_root_only_with_tree(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(SessionModel(
            trace_id="trace-map",
            title="Session",
            started_at="2026-04-22 10:00:00",
            last_seen="2026-04-22 10:00:03",
        ))
        session.add(SessionSpan(
            trace_id="trace-map", span_id="root", parent_id=None,
            name="prompt", kind="internal",
            start_time="2026-04-22 10:00:00",
        ))
        session.add(SessionSpan(
            trace_id="trace-map", span_id="child", parent_id="root",
            name="tool.Read", kind="internal",
            start_time="2026-04-22 10:00:01",
        ))
        session.commit()

    body = flask_client.get("/api/sessions/trace-map/map?shallow=1").get_json()
    assert body["trace_id"] == "trace-map"
    assert body["span_count_total"] == 2
    assert body["span_count"] == 2
    assert [s["span_id"] for s in body["spans"]] == ["root"]
    assert len(body["tree"]) == 1
    root = body["tree"][0]
    assert root["data"]["span_id"] == "root"
    assert root["leaf"] is False
    assert root["data"]["child_count"] == 1


# ── POST /api/sessions/batch-delete ─────────────────────────

def test_batch_delete_requires_list(flask_client, tmp_db):
    resp = flask_client.post("/api/sessions/batch-delete",
                               json={"trace_ids": "not-a-list"})
    assert resp.status_code == 400


def test_batch_delete_empty_list_rejected(flask_client, tmp_db):
    resp = flask_client.post("/api/sessions/batch-delete",
                               json={"trace_ids": []})
    assert resp.status_code == 400


def test_batch_delete_removes_from_all_session_tables(flask_client, tmp_db):
    _seed_session("t1")
    _seed_session("t2")

    resp = flask_client.post(
        "/api/sessions/batch-delete",
        json={"trace_ids": ["t1", "t2"]},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["processed"] == 2
    # Two sessions seeded with 1 row each in every session-keyed table.
    assert body["deleted"] == {
        "sessions": 2, "spans": 2, "trace_map": 2, "turn_usage": 2,
        "session_repos": 2, "skill_reads": 2, "plan_sessions": 2,
        "rule_triggers": 2, "prompt_images": 0,
    }
    _assert_no_session_residue("t1")
    _assert_no_session_residue("t2")


def test_batch_delete_unknown_ids_are_noops(flask_client, tmp_db):
    """Unknown trace_ids contribute zero to deleted counts but don't error."""
    resp = flask_client.post(
        "/api/sessions/batch-delete",
        json={"trace_ids": ["never-seen-id"]},
    )
    body = resp.get_json()
    assert body["ok"] is True
    assert body["processed"] == 1
    assert body["deleted"]["sessions"] == 0


def test_batch_delete_rejects_non_string_elements(flask_client, tmp_db):
    resp = flask_client.post(
        "/api/sessions/batch-delete",
        json={"trace_ids": [123, "also"]},
    )
    assert resp.status_code == 400


# ── DELETE /api/sessions/<id> ────────────────────────────────

def test_session_delete_returns_zeros_for_unknown(flask_client, tmp_db):
    resp = flask_client.delete("/api/sessions/never-seen")
    body = resp.get_json()
    assert body["ok"] is True
    assert body["trace_id"] == "never-seen"
    assert body["deleted"]["sessions"] == 0


def test_session_delete_removes_full_session(flask_client, tmp_db):
    _seed_session("to-delete")
    resp = flask_client.delete("/api/sessions/to-delete")
    body = resp.get_json()
    assert body["ok"] is True
    assert body["deleted"] == {
        "sessions": 1, "spans": 1, "trace_map": 1, "turn_usage": 1,
        "session_repos": 1, "skill_reads": 1, "plan_sessions": 1,
        "rule_triggers": 1, "prompt_images": 0,
    }
    # No table that carries the session id may keep a row behind.
    _assert_no_session_residue("to-delete")


# ── GET /api/ingest-errors ──────────────────────────────────

def test_ingest_errors_missing_file_returns_empty(
        flask_client, tmp_path, monkeypatch):
    from lib import hook_plugin as hp
    monkeypatch.setattr(hp, "_INGEST_ERROR_LOG",
                        str(tmp_path / "nope.jsonl"))
    resp = flask_client.get("/api/ingest-errors")
    body = resp.get_json()
    assert body["rows"] == []
    # Aggregation buckets still present.
    assert body["by_endpoint"] == {}
    assert body["by_error_type"] == {}


def test_ingest_errors_reads_recent_lines(
        flask_client, tmp_path, monkeypatch):
    from lib import hook_plugin as hp
    log = tmp_path / "ingest.jsonl"
    log.write_text("\n".join([
        json.dumps({"endpoint": "/api/session-spans",
                    "error_type": "ValueError", "gave_up": True}),
        json.dumps({"endpoint": "/api/session-spans",
                    "error_type": "TimeoutError", "gave_up": False}),
        "",  # blank line — skipped
        "{ not json",  # bad JSON — skipped
    ]) + "\n")
    monkeypatch.setattr(hp, "_INGEST_ERROR_LOG", str(log))

    resp = flask_client.get("/api/ingest-errors")
    body = resp.get_json()
    assert len(body["rows"]) == 2
    assert body["by_endpoint"]["/api/session-spans"] == 2
    assert set(body["by_error_type"].keys()) == {
        "ValueError", "TimeoutError",
    }
    assert body["by_gave_up"]["true"] == 1
    assert body["by_gave_up"]["false"] == 1


def test_ingest_errors_endpoint_filter(
        flask_client, tmp_path, monkeypatch):
    from lib import hook_plugin as hp
    log = tmp_path / "ingest.jsonl"
    log.write_text("\n".join([
        json.dumps({"endpoint": "/api/a", "error_type": "E"}),
        json.dumps({"endpoint": "/api/b", "error_type": "E"}),
    ]) + "\n")
    monkeypatch.setattr(hp, "_INGEST_ERROR_LOG", str(log))

    resp = flask_client.get("/api/ingest-errors?endpoint=/api/a")
    body = resp.get_json()
    endpoints = {r["endpoint"] for r in body["rows"]}
    assert endpoints == {"/api/a"}
    # Aggregations still cover ALL rows read from the tail.
    assert body["by_endpoint"]["/api/b"] == 1


def test_ingest_errors_gave_up_filter(
        flask_client, tmp_path, monkeypatch):
    from lib import hook_plugin as hp
    log = tmp_path / "ingest.jsonl"
    log.write_text("\n".join([
        json.dumps({"endpoint": "e", "gave_up": True}),
        json.dumps({"endpoint": "e", "gave_up": False}),
    ]) + "\n")
    monkeypatch.setattr(hp, "_INGEST_ERROR_LOG", str(log))

    resp = flask_client.get("/api/ingest-errors?gave_up=true")
    body = resp.get_json()
    gave_ups = {r["gave_up"] for r in body["rows"]}
    assert gave_ups == {True}


def test_ingest_errors_clamps_limit(
        flask_client, tmp_path, monkeypatch):
    from lib import hook_plugin as hp
    log = tmp_path / "ingest.jsonl"
    log.write_text("\n".join(
        json.dumps({"endpoint": f"ep{i}"}) for i in range(5)
    ) + "\n")
    monkeypatch.setattr(hp, "_INGEST_ERROR_LOG", str(log))

    resp = flask_client.get("/api/ingest-errors?limit=abc")
    # Invalid limit → default 50 (not a crash).
    assert resp.status_code == 200
    assert len(resp.get_json()["rows"]) == 5  # all 5 fit under default


# ── /api/session-status ──────────────────────────────────────

def test_session_status_rejects_non_object(flask_client, tmp_db):
    resp = flask_client.post('/api/session-status',
                              json=['not', 'an', 'object'])
    assert resp.status_code == 400
    assert 'object' in resp.get_json()['error']


def test_session_status_requires_trace_id(flask_client, tmp_db):
    resp = flask_client.post('/api/session-status',
                              json={'model': 'claude-opus-4-7[1m]'})
    assert resp.status_code == 400


def test_session_status_happy_path_updates_model(flask_client, tmp_db):
    """End-to-end: the statusline POST lands, /api/sessions/<id>
    reflects the variant suffix in the model field, and the ctx%
    computation resolves to the 1M window via infer_window."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, "
            "model, peak_context_tokens, context_window_tokens) "
            "VALUES ('t-status', '2026-04-24T00:00:00', '2026-04-24T00:00:00', "
            "'claude-opus-4-7', 100000, 200000)"
        )
        conn.commit()
    finally:
        conn.close()

    resp = flask_client.post('/api/session-status', json={
        'trace_id': 't-status',
        'model': 'claude-opus-4-7[1m]',
        'context_used_tokens': 180000,
        'context_window_tokens': 1_000_000,
    })
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True}

    detail = flask_client.get('/api/sessions/t-status?shallow=1').get_json()
    assert detail['model'] == 'claude-opus-4-7[1m]'
    assert detail['peak_context_tokens'] == 180000
    # infer_window should now resolve to 1M given the variant-bracketed
    # id, so ctx% is ~18% rather than the misleading 90% it was with the
    # bare base model.
    assert detail['context_window_tokens'] == 1_000_000
    assert detail['context_pct'] is not None and detail['context_pct'] < 25


# NOTE: POST /api/sessions/<id>/reconcile-prompts was removed in the
# append-only capture refactor — stray `/workflows` prompt placeholders are
# now dropped by the serve-time merge (lib/trace/merge.py:_drop_stale_blockers),
# covered by tests/trace/test_pending_handoff.py.
