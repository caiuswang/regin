"""Unit tests for the declared notification event bus (lib.agent_messages.events).

`emit` routes every declared kind through `store.record_message` — but only
when the kind is enabled (registry default, `settings.agent_messages.events`
override, or a legacy `push_*_events` flag), supports once-dedup + severity
override, and is best-effort (a store failure is swallowed). `catalog`
enumerates the whole registry. The store calls are captured so nothing
touches the DB, and the three new producers are exercised against the same
capture.
"""

from __future__ import annotations

import pytest

from lib.agent_messages import events
from lib.orm.models.agent_messages import MESSAGE_TYPES
from lib.settings import settings


@pytest.fixture
def recorded(monkeypatch):
    """Capture record_message; control live_keyed_message + dismiss_keyed."""
    calls: list[dict] = []
    live: dict = {}
    dismissed: list[tuple] = []

    def _record(**kw):
        calls.append(kw)
        if kw.get("msg_key"):
            live[kw["msg_key"]] = {"body": kw["body"]}
        return {"id": len(calls), **kw}

    def _live(trace_id, key):
        return live.get(key)

    def _dismiss(trace_id, key):
        dismissed.append((trace_id, key))
        return int(live.pop(key, None) is not None)

    from lib.agent_messages import store
    monkeypatch.setattr(store, "record_message", _record)
    monkeypatch.setattr(store, "live_keyed_message", _live)
    monkeypatch.setattr(store, "dismiss_keyed", _dismiss)
    # Clean override map + legacy flags off, so each test starts from defaults.
    monkeypatch.setattr(settings.agent_messages, "events", {})
    monkeypatch.setattr(settings.agent_messages, "push_permission_events", False)
    monkeypatch.setattr(settings.agent_messages, "push_plan_events", False)
    return {"calls": calls, "live": live, "dismissed": dismissed}


def _override(monkeypatch, **kinds):
    monkeypatch.setattr(settings.agent_messages, "events", dict(kinds))


# ── Catalog ──────────────────────────────────────────────────

def test_catalog_enumerates_whole_registry(recorded):
    rows = events.catalog()
    assert len(rows) == len(events.REGISTRY)
    kinds = {r["kind"] for r in rows}
    assert kinds == set(events.REGISTRY)
    for r in rows:
        assert r["severity"] in MESSAGE_TYPES
        assert set(r) == {"kind", "severity", "default_enabled",
                          "enabled", "summary"}


def test_catalog_reflects_current_enablement(recorded, monkeypatch):
    _override(monkeypatch, **{"grade.finished": True})
    by_kind = {r["kind"]: r["enabled"] for r in events.catalog()}
    assert by_kind["grade.finished"] is True          # override on
    assert by_kind["permission.pending"] is False     # legacy flag off


# ── Enablement precedence ────────────────────────────────────

def test_unknown_kind_is_disabled_and_noop(recorded):
    assert events.is_enabled("nope.nope") is False
    assert events.emit("nope.nope", trace_id="s1", body="x") is None
    assert recorded["calls"] == []


def test_default_off_kind_is_noop_without_override(recorded):
    # grade.finished defaults off
    assert events.is_enabled("grade.finished") is False
    assert events.emit("grade.finished", trace_id="s1", body="x") is None
    assert recorded["calls"] == []


def test_override_enables_default_off_kind(recorded, monkeypatch):
    _override(monkeypatch, **{"grade.finished": True})
    assert events.is_enabled("grade.finished") is True
    data = events.emit("grade.finished", trace_id="s1", body="done")
    assert data is not None
    assert len(recorded["calls"]) == 1


def test_default_on_kind_emits_without_override(recorded):
    # proposal.ready defaults on
    assert events.is_enabled("proposal.ready") is True
    assert events.emit("proposal.ready", trace_id="s1", body="x") is not None


def test_legacy_flag_gates_permission_kind(recorded, monkeypatch):
    assert events.is_enabled("permission.pending") is False
    monkeypatch.setattr(settings.agent_messages, "push_permission_events", True)
    assert events.is_enabled("permission.pending") is True


def test_override_wins_over_legacy_flag(recorded, monkeypatch):
    monkeypatch.setattr(settings.agent_messages, "push_permission_events", True)
    _override(monkeypatch, **{"permission.pending": False})
    assert events.is_enabled("permission.pending") is False


def test_missing_trace_id_is_noop(recorded):
    assert events.emit("proposal.ready", trace_id=None, body="x") is None
    assert recorded["calls"] == []


# ── Payload routing ──────────────────────────────────────────

def test_emit_routes_fields_to_record_message(recorded):
    events.emit("proposal.ready", trace_id="s1", body="B", title="T",
                key="k1", links=["/repos/x/topics"], span_id="sp1")
    (call,) = recorded["calls"]
    assert call["trace_id"] == "s1"
    assert call["msg_type"] == "result"        # proposal.ready severity
    assert call["title"] == "T"
    assert call["body"] == "B"
    assert call["msg_key"] == "k1"
    assert call["links"] == ["/repos/x/topics"]
    assert call["span_id"] == "sp1"


def test_severity_override(recorded):
    events.emit("proposal.ready", trace_id="s1", body="x", severity="blocker")
    assert recorded["calls"][0]["msg_type"] == "blocker"


# ── once-dedup ───────────────────────────────────────────────

def test_once_skips_when_live_card_exists(recorded):
    assert events.emit("proposal.ready", trace_id="s1", body="a",
                       key="p:1", once=True) is not None
    # second emit with a live card present is skipped
    assert events.emit("proposal.ready", trace_id="s1", body="b",
                       key="p:1", once=True) is None
    assert len(recorded["calls"]) == 1


def test_once_false_supersedes_each_time(recorded):
    events.emit("proposal.ready", trace_id="s1", body="a", key="p:1")
    events.emit("proposal.ready", trace_id="s1", body="b", key="p:1")
    assert len(recorded["calls"]) == 2


# ── Best-effort isolation ────────────────────────────────────

def test_emit_swallows_store_failure(monkeypatch, recorded):
    def _boom(**kw):
        raise RuntimeError("db down")
    from lib.agent_messages import store
    monkeypatch.setattr(store, "record_message", _boom)
    # must not raise
    assert events.emit("proposal.ready", trace_id="s1", body="x") is None


# ── resolve + url builders ───────────────────────────────────

def test_resolve_dismisses_keyed(recorded):
    events.resolve("s1", "k1")
    assert recorded["dismissed"] == [("s1", "k1")]


def test_resolve_noop_without_trace_or_key(recorded):
    events.resolve(None, "k1")
    events.resolve("s1", "")
    assert recorded["dismissed"] == []


def test_url_builders():
    assert events.topics_url("/home/me/myrepo").endswith("/repos/myrepo/topics")
    assert events.session_url("abc") == "/trace/sessions/abc"


# ── Producers ────────────────────────────────────────────────

def test_producer_proposal_ready(recorded, monkeypatch):
    from lib.topics import proposal_external as pe

    class _Ctx:
        trace_id = "topic-proposal-42"
        proposal_id = "42"
        agent = "claude"
        out_dir = __import__("pathlib").Path(
            "/tmp/myrepo/.regin/topics/proposals/42")

    pe._notify_proposal_ready(_Ctx())
    (call,) = recorded["calls"]
    assert call["msg_type"] == "result"
    assert call["msg_key"] == "proposal-ready:42"
    # action link deep-links to the specific proposal run
    assert call["links"] == [{"label": "Open proposal run",
                              "href": "/repos/myrepo/topics?tab=proposals&proposal=42"}]
    # no recorded agent_trace_id for this run dir → footer falls back to the
    # synthetic wrapper trace rather than an empty trace_id
    assert call["trace_id"] == "topic-proposal-42"
    assert "42" in call["body"]


def test_producer_content_drift(recorded):
    from lib.topics import wiki_debt
    row = {"topic_id": "trace-merge", "status": "drifted",
           "drifted_paths": ["lib/trace/merge.py"], "proposal_id": "cd-1"}
    wiki_debt._notify_drift("/tmp/myrepo", row)
    (call,) = recorded["calls"]
    assert call["msg_type"] == "warning"
    # key is repo-scoped so a same-named topic in another repo can't collide
    assert call["msg_key"] == "content-drift:myrepo:trace-merge"
    assert call["links"] == [{"label": "Review in Topics",
                              "href": "/repos/myrepo/topics"}]
    assert "lib/trace/merge.py" in call["body"]
    assert "refresh proposal is queued" in call["body"]


def test_producer_content_drift_key_scoped_per_repo(recorded):
    from lib.topics import wiki_debt
    row = {"topic_id": "auth", "status": "drifted", "drifted_paths": []}
    wiki_debt._notify_drift("/srv/repoA", dict(row))
    wiki_debt._notify_drift("/srv/repoB", dict(row))
    keys = {c["msg_key"] for c in recorded["calls"]}
    # two repos, same topic id → distinct cards (no silent collision)
    assert keys == {"content-drift:repoA:auth", "content-drift:repoB:auth"}


def test_producer_content_drift_no_proposal_omits_queued(recorded):
    from lib.topics import wiki_debt
    row = {"topic_id": "t1", "status": "drifted", "drifted_paths": [],
           "proposal_id": None}
    wiki_debt._notify_drift("/tmp/myrepo", row)
    assert "queued" not in recorded["calls"][0]["body"]


def test_resolve_drift_card_dismisses_scoped_key(recorded):
    from lib.topics import content_drift
    content_drift.resolve_drift_card("/tmp/myrepo", "trace-merge")
    assert recorded["dismissed"] == [
        ("wiki-debt", "content-drift:myrepo:trace-merge")]


def test_producer_grade_finished_emits(recorded, monkeypatch):
    _override(monkeypatch, **{"grade.finished": True})
    from lib.grader import service

    class _G:
        verdict = "pass"
    service._maybe_notify_grade("sess-9", {"correctness": _G()}, is_test=0)
    (call,) = recorded["calls"]
    assert call["msg_type"] == "note"
    assert call["msg_key"] == "grade-finished:sess-9"
    assert call["links"] == [{"label": "View session trace",
                              "href": "/trace/sessions/sess-9"}]
    assert "correctness: pass" in call["body"]


def test_producer_grade_finished_skips_test_grade(recorded, monkeypatch):
    _override(monkeypatch, **{"grade.finished": True})
    from lib.grader import service

    class _G:
        verdict = "pass"
    service._maybe_notify_grade("sess-9", {"correctness": _G()}, is_test=1)
    assert recorded["calls"] == []
