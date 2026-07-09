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


# ── structural map keeps the compact memory.recall labels ──────────
# Regression: an injected <skill_experience> block must stay visible in the
# trace detail on a FRESH load. It renders from a `memory.recall` span whose
# source/skill_id/hit_count distinguish it from generic recalled experience;
# the structural /map strips attributes, so those compact labels must be on
# the keep-list or the row degrades to plain "recalled experience" on reload.

def test_kept_map_attrs_preserves_skill_experience_labels():
    from web.blueprints.trace.sessions import _kept_map_attrs
    kept = _kept_map_attrs({
        "source": "skill_experience", "skill_id": "playwright-skill",
        "hit_count": 3, "block": "<skill_experience>x</skill_experience>",
        "hits": [{"id": "a"}], "unrelated": 1,
    })
    assert kept == {"source": "skill_experience",
                    "skill_id": "playwright-skill", "hit_count": 3}
    # heavy/unknown attrs are dropped (loaded lazily via /spans/<id>/content)
    assert "block" not in kept and "hits" not in kept and "unrelated" not in kept


def test_structural_map_keeps_skill_experience_attrs(flask_client, tmp_db):
    with SessionLocal() as session:
        session.add(SessionModel(
            trace_id="trace-se", title="S",
            started_at="2026-04-22 10:00:00", last_seen="2026-04-22 10:00:03"))
        session.add(SessionSpan(
            trace_id="trace-se", span_id="prompt-se", parent_id=None,
            name="prompt", kind="internal", start_time="2026-04-22 10:00:00"))
        session.add(SessionSpan(
            trace_id="trace-se", span_id="mr-se", parent_id=None,
            name="memory.recall", kind="internal",
            start_time="2026-04-22 10:00:01",
            attributes=json.dumps({
                "source": "skill_experience", "skill_id": "playwright-skill",
                "hit_count": 2,
                "block": "<skill_experience>x</skill_experience>",
            })))
        session.commit()

    body = flask_client.get("/api/sessions/trace-se/map").get_json()
    mr = next(s for s in body["spans"] if s["span_id"] == "mr-se")
    attrs = mr.get("attributes") or {}
    assert attrs.get("source") == "skill_experience"
    assert attrs.get("skill_id") == "playwright-skill"
    assert attrs.get("hit_count") == 2
    assert "block" not in attrs  # heavy attr stripped, fetched lazily


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
        "session_repos": 2, "session_tags": 0, "skill_reads": 2,
        "plan_sessions": 2, "rule_triggers": 2, "prompt_images": 0,
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
        "session_repos": 1, "session_tags": 0, "skill_reads": 1,
        "plan_sessions": 1, "rule_triggers": 1, "prompt_images": 0,
    }
    # No table that carries the session id may keep a row behind.
    _assert_no_session_residue("to-delete")


# ── POST /api/sessions/<id>/close ────────────────────────────

def test_session_close_marks_ended_manual(flask_client, tmp_db):
    _seed_session("to-close")  # seeded with status=NULL, ended_at=NULL
    resp = flask_client.post("/api/sessions/to-close/close")
    body = resp.get_json()
    assert body["ok"] is True
    assert body["status"] == "ended"
    assert body["ended_reason"] == "manual"
    assert body["ended_at"]  # a now() timestamp was stamped
    with SessionLocal() as session:
        row = session.get(SessionModel, "to-close")
        assert row.status == "ended"
        assert row.ended_reason == "manual"
        assert row.ended_at is not None
    # Trace data is kept — close is not a delete.
    with SessionLocal() as session:
        spans = session.exec(
            select(SessionSpan).where(SessionSpan.trace_id == "to-close")
        ).all()
        assert len(spans) == 1


def test_session_close_unknown_returns_404(flask_client, tmp_db):
    resp = flask_client.post("/api/sessions/never-seen/close")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["ok"] is False


def test_session_close_preserves_natural_ended_at(flask_client, tmp_db):
    """A session that already ended naturally keeps its ended_at; only the
    reason flips to 'manual' (the CASE stays 'ended' either way)."""
    with SessionLocal() as session:
        session.add(SessionModel(
            trace_id="already-ended", title="S",
            started_at="2026-04-22 10:00:00",
            last_seen="2026-04-22 10:05:00",
            status="ended", ended_at="2026-04-22 10:05:00",
            ended_reason="clear",
        ))
        session.commit()
    resp = flask_client.post("/api/sessions/already-ended/close")
    body = resp.get_json()
    assert body["ok"] is True
    assert body["ended_at"] == "2026-04-22 10:05:00"
    with SessionLocal() as session:
        row = session.get(SessionModel, "already-ended")
        assert row.ended_at == "2026-04-22 10:05:00"
        assert row.ended_reason == "manual"


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


# ── _fetch_session_task_list (characterization) ─────────────

def _seed_task_span(trace_id, span_id, name, start_time, attrs):
    with SessionLocal() as session:
        session.add(SessionSpan(
            trace_id=trace_id, span_id=span_id, parent_id=None,
            name=name, kind="internal", start_time=start_time,
            attributes=json.dumps(attrs),
        ))
        session.commit()


def test_fetch_task_list_none_when_no_task_spans(tmp_db):
    from web.blueprints.trace.sessions import _fetch_session_task_list
    # No TaskCreate/TaskUpdate rows at all -> None.
    assert _fetch_session_task_list("nope") is None


def test_fetch_task_list_none_when_rows_lack_task_id(tmp_db):
    from web.blueprints.trace.sessions import _fetch_session_task_list
    _seed_task_span("t-noid", "s1", "tool.TaskCreate",
                    "2026-04-22 10:00:00", {"subject": "orphan"})
    # Rows present but none carry a task_id -> None.
    assert _fetch_session_task_list("t-noid") is None


def test_fetch_task_list_current_span_tracks_final_status_flip_flop(tmp_db):
    from web.blueprints.trace.sessions import _fetch_session_task_list
    tid = "t-flip"
    _seed_task_span(tid, "A", "tool.TaskCreate", "2026-04-22 10:00:00",
                    {"task_id": "1", "subject": "Write", "status": "pending"})
    _seed_task_span(tid, "B", "tool.TaskUpdate", "2026-04-22 10:00:01",
                    {"task_id": "1", "status": "in_progress"})
    _seed_task_span(tid, "C", "tool.TaskUpdate", "2026-04-22 10:00:02",
                    {"task_id": "1", "status": "completed"})
    _seed_task_span(tid, "D", "tool.TaskUpdate", "2026-04-22 10:00:03",
                    {"task_id": "1", "status": "in_progress"})

    out = _fetch_session_task_list(tid)
    assert out is not None
    final = out["final"]
    assert len(final) == 1
    entry = final[0]
    assert entry["status"] == "in_progress"
    # current_span_id is the LATEST span that set the FINAL status, not
    # merely the last update -> D, not C.
    assert entry["current_span_id"] == "D"
    assert entry["subject"] == "Write"
    assert entry["created_span_id"] == "A"


def test_fetch_task_list_subject_first_non_empty_wins(tmp_db):
    from web.blueprints.trace.sessions import _fetch_session_task_list
    tid = "t-subj"
    _seed_task_span(tid, "A", "tool.TaskCreate", "2026-04-22 10:00:00",
                    {"task_id": "1", "subject": "First"})
    _seed_task_span(tid, "B", "tool.TaskUpdate", "2026-04-22 10:00:01",
                    {"task_id": "1", "subject": "Second", "status": "completed"})

    out = _fetch_session_task_list(tid)
    # First non-empty subject wins; later subjects do not overwrite it.
    assert out["final"][0]["subject"] == "First"


def test_fetch_task_list_pending_falls_back_to_created_span(tmp_db):
    from web.blueprints.trace.sessions import _fetch_session_task_list
    tid = "t-pending"
    _seed_task_span(tid, "A", "tool.TaskCreate", "2026-04-22 10:00:00",
                    {"task_id": "1", "subject": "Never updated"})

    entry = _fetch_session_task_list(tid)["final"][0]
    # Never got a status -> defaults to 'pending', current_span_id falls
    # back to the TaskCreate span.
    assert entry["status"] == "pending"
    assert entry["current_span_id"] == "A"


def test_fetch_task_list_sort_numeric_then_nondigit_last(tmp_db):
    from web.blueprints.trace.sessions import _fetch_session_task_list
    tid = "t-sort"
    _seed_task_span(tid, "A", "tool.TaskCreate", "2026-04-22 10:00:00",
                    {"task_id": "10", "subject": "ten"})
    _seed_task_span(tid, "B", "tool.TaskCreate", "2026-04-22 10:00:01",
                    {"task_id": "2", "subject": "two"})
    _seed_task_span(tid, "C", "tool.TaskCreate", "2026-04-22 10:00:02",
                    {"task_id": "foo", "subject": "fff"})

    final = _fetch_session_task_list(tid)["final"]
    # Numeric ids sort numerically; non-digit ids sink to the end.
    assert [t["task_id"] for t in final] == ["2", "10", "foo"]


def test_fetch_task_list_event_shape_omits_absent_fields(tmp_db):
    from web.blueprints.trace.sessions import _fetch_session_task_list
    tid = "t-events"
    _seed_task_span(tid, "A", "tool.TaskCreate", "2026-04-22 10:00:00",
                    {"task_id": "1", "subject": "Subj"})
    _seed_task_span(tid, "B", "tool.TaskUpdate", "2026-04-22 10:00:01",
                    {"task_id": "1", "status": "completed"})

    events = _fetch_session_task_list(tid)["events"]
    assert len(events) == 2
    create_evt, update_evt = events
    # Per-span, append-only; absent fields are OMITTED, not null.
    assert create_evt == {
        "span_id": "A", "timestamp": "2026-04-22 10:00:00",
        "task_id": "1", "subject": "Subj",
    }
    assert "status" not in create_evt
    assert update_evt == {
        "span_id": "B", "timestamp": "2026-04-22 10:00:01",
        "task_id": "1", "status": "completed",
    }
    assert "subject" not in update_evt
