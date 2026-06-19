"""The topic-routing feedback loop: record an injected `<topic_context>`
banner, stamp it with the `InjectedRelated` grade verdict, and withhold a
route that has been graded irrelevant often enough. The topic analog of the
memory engagement loop in `test_feedback.py`.
"""

from __future__ import annotations

import lib.memory as memory
from hook_manager.handlers.memory_recall import _route_topic, _topic_suppressed
from lib.grader.models import AxisGrade
from lib.grader.service import _maybe_apply_injection_relevance
from lib.settings import settings


# ── helpers ───────────────────────────────────────────────────

def _seed(topic_id: str, verdicts: list[str], *, start: int = 0) -> None:
    """Record `len(verdicts)` injections of `topic_id` across distinct
    sessions and stamp each with its verdict."""
    store = memory.get_store()
    for i, verdict in enumerate(verdicts, start=start):
        sid = f"sess-{topic_id}-{i}"
        store.record_topic_injection(sid, topic_id)
        store.apply_topic_relevance(sid, verdict)


def _summary_for(topic_id: str) -> dict:
    """The summary row for one topic (status/decision/fail_rate/…)."""
    rows = memory.get_store().topic_relevance_summary()
    return next(r for r in rows if r["topic_id"] == topic_id)


class _Ev:
    """Minimal evidence stand-in: only `.session` is read by the wiring."""

    def __init__(self, is_test: int = 0):
        self.session = {"is_test": is_test}


# ── recording + stamping ──────────────────────────────────────

def test_record_topic_injection_is_idempotent():
    store = memory.get_store()
    store.record_topic_injection("s1", "topicA")
    store.record_topic_injection("s1", "topicA")  # re-route same turn
    assert store.topic_relevance_stats("topicA") == (0, 0)  # nothing scored yet


def test_apply_relevance_stamps_unscored_once():
    store = memory.get_store()
    store.record_topic_injection("s1", "topicA")
    assert store.apply_topic_relevance("s1", "fail") == 1
    # re-applying stamps nothing — scored_at makes it idempotent
    assert store.apply_topic_relevance("s1", "satisfied") == 0
    assert store.topic_relevance_stats("topicA") == (1, 1)


def test_stats_count_only_scored_and_only_fails():
    _seed("topicA", ["fail", "fail", "satisfied", "needs_revision"])
    store = memory.get_store()
    store.record_topic_injection("unscored", "topicA")  # excluded (no verdict)
    assert store.topic_relevance_stats("topicA") == (2, 4)


# ── the human gate: threshold proposes, decision withholds ────

def test_over_threshold_proposes_but_does_not_withhold():
    _seed("topicA", ["fail", "fail", "fail", "satisfied"])  # 3/4 over the bar
    # the threshold only PROPOSES — nothing is withheld without a decision
    assert _topic_suppressed("topicA", settings.agent_memory) is False
    row = _summary_for("topicA")
    assert row["status"] == "proposed" and row["decision"] is None


def test_approved_decision_withholds():
    _seed("topicA", ["fail", "fail", "fail"])
    memory.get_store().set_topic_decision("topicA", "suppressed")
    assert _topic_suppressed("topicA", settings.agent_memory) is True
    assert _summary_for("topicA")["status"] == "suppressed"


def test_allowed_decision_pins_route_on_despite_fails():
    _seed("topicA", ["fail", "fail", "fail", "fail"])  # would be proposed
    memory.get_store().set_topic_decision("topicA", "allowed")
    assert _topic_suppressed("topicA", settings.agent_memory) is False
    assert _summary_for("topicA")["status"] == "allowed"


def test_clearing_decision_returns_to_proposed():
    _seed("topicA", ["fail", "fail", "fail"])
    store = memory.get_store()
    store.set_topic_decision("topicA", "suppressed")
    store.set_topic_decision("topicA", "auto")  # clear
    assert store.topic_decision("topicA") is None
    assert _topic_suppressed("topicA", settings.agent_memory) is False
    assert _summary_for("topicA")["status"] == "proposed"


def test_force_suppress_without_threshold():
    _seed("topicB", ["satisfied"])  # nowhere near the bar
    memory.get_store().set_topic_decision("topicB", "suppressed")
    assert _topic_suppressed("topicB", settings.agent_memory) is True


def test_set_topic_decision_rejects_unknown_value():
    import pytest
    with pytest.raises(ValueError):
        memory.get_store().set_topic_decision("topicA", "nonsense")


def test_unknown_topic_not_suppressed():
    assert _topic_suppressed("never-seen", settings.agent_memory) is False


def test_feature_flag_off_never_suppresses(monkeypatch):
    memory.get_store().set_topic_decision("topicA", "suppressed")
    monkeypatch.setattr(settings.agent_memory, "topic_relevance_feedback",
                        False)
    assert _topic_suppressed("topicA", settings.agent_memory) is False


# ── _route_topic withholds only an approved route ─────────────

def _route(topic_id, monkeypatch):
    monkeypatch.setattr("lib.topics.route.match_topic",
                        lambda repo, query: {"id": topic_id, "label": topic_id})
    monkeypatch.setattr(settings.agent_memory, "topic_route_inject", True)
    return _route_topic("anything", None, settings.agent_memory)


def test_route_topic_routes_proposed_until_approved(monkeypatch):
    _seed("topicA", ["fail", "fail", "fail"])  # proposed, not yet approved
    assert _route("topicA", monkeypatch) is not None  # still routes
    memory.get_store().set_topic_decision("topicA", "suppressed")
    assert _route("topicA", monkeypatch) is None  # withheld after sign-off


def test_route_topic_passes_healthy(monkeypatch):
    _seed("topicHealthy", ["satisfied", "satisfied", "fail"])  # 1/3 fail
    routed = _route("topicHealthy", monkeypatch)
    assert routed and routed["id"] == "topicHealthy"


# ── grade-time wiring ─────────────────────────────────────────

def _grade(verdict: str = "fail") -> AxisGrade:
    return AxisGrade(axis="injectedrelated", verdict=verdict, tier="deep")


def test_grade_wiring_stamps_the_aspect_verdict():
    memory.get_store().record_topic_injection("tr1", "topicA")
    _maybe_apply_injection_relevance(
        "tr1", {"injectedrelated": _grade("fail")}, _Ev(), is_test=0)
    assert memory.get_store().topic_relevance_stats("topicA") == (1, 1)


def test_grade_wiring_skips_test_sessions():
    memory.get_store().record_topic_injection("tr1", "topicA")
    _maybe_apply_injection_relevance(
        "tr1", {"injectedrelated": _grade("fail")}, _Ev(is_test=1), is_test=0)
    assert memory.get_store().topic_relevance_stats("topicA") == (0, 0)


def test_grade_wiring_noop_without_the_aspect():
    memory.get_store().record_topic_injection("tr1", "topicA")
    # only correctness graded — no injectedrelated aspect present
    _maybe_apply_injection_relevance(
        "tr1", {"correctness": _grade("satisfied")}, _Ev(), is_test=0)
    assert memory.get_store().topic_relevance_stats("topicA") == (0, 0)


def test_grade_wiring_respects_custom_aspect_key(monkeypatch):
    monkeypatch.setattr(settings.agent_memory, "topic_relevance_aspect",
                        "injrel")
    memory.get_store().record_topic_injection("tr1", "topicA")
    _maybe_apply_injection_relevance(
        "tr1", {"injrel": _grade("fail")}, _Ev(), is_test=0)
    assert memory.get_store().topic_relevance_stats("topicA") == (1, 1)


def test_grade_wiring_never_raises(monkeypatch):
    # a store blow-up must not propagate out of the best-effort wiring
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(memory, "get_store", _boom)
    _maybe_apply_injection_relevance(
        "tr1", {"injectedrelated": _grade("fail")}, _Ev(), is_test=0)


# ── query-local route suppression (topic negatives) ──────────

def _stub_store():
    from lib.memory.store import SqliteMemoryStore
    from tests.memory.test_store import _StubEmbedder
    return SqliteMemoryStore(embedder=_StubEmbedder(
        {"ALPHAQ": [1.0, 0.0, 0.0], "BETAQ": [0.0, 1.0, 0.0]}))


def test_fail_verdict_records_topic_negative_when_enabled(monkeypatch):
    """A `fail`-graded banner with a recorded query becomes a topic negative,
    gated on suppression being enabled."""
    from sqlmodel import select
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import TopicNegative

    monkeypatch.setattr(settings.agent_memory,
                        "topic_negative_suppress_sim", 0.5)
    store = _stub_store()
    store.record_topic_injection("s-neg", "topicX", query="ALPHAQ context")
    store.apply_topic_relevance("s-neg", "fail")

    with MemorySessionLocal() as session:
        rows = session.exec(select(TopicNegative)
                            .where(TopicNegative.topic_id == "topicX")).all()
    assert len(rows) == 1 and rows[0].source_session == "s-neg"


def test_no_topic_negative_when_disabled(monkeypatch):
    from sqlmodel import select
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import TopicNegative

    monkeypatch.setattr(settings.agent_memory,
                        "topic_negative_suppress_sim", 0.0)
    store = _stub_store()
    store.record_topic_injection("s-off", "topicX", query="ALPHAQ context")
    store.apply_topic_relevance("s-off", "fail")

    with MemorySessionLocal() as session:
        assert session.exec(select(TopicNegative)).all() == []


def test_topic_route_suppressed_is_query_local(monkeypatch):
    """A topic negative withholds the route for a similar query, not a
    dissimilar one."""
    monkeypatch.setattr(settings.agent_memory,
                        "topic_negative_suppress_sim", 0.5)
    store = _stub_store()
    store.add_topic_negatives("s", [("topicX", "ALPHAQ failing prompt")])

    assert store.topic_route_suppressed("topicX", "ALPHAQ again") is True
    assert store.topic_route_suppressed("topicX", "BETAQ elsewhere") is False


def test_topic_route_suppressed_respects_allowed_pin(monkeypatch):
    """A human `allowed` pin overrides negative-based suppression."""
    monkeypatch.setattr(settings.agent_memory,
                        "topic_negative_suppress_sim", 0.5)
    store = _stub_store()
    store.add_topic_negatives("s", [("topicX", "ALPHAQ failing prompt")])
    store.set_topic_decision("topicX", "allowed")

    assert store.topic_route_suppressed("topicX", "ALPHAQ again") is False


def test_topic_route_suppressed_off_when_threshold_zero(monkeypatch):
    """With the threshold at 0, negatives never withhold a route."""
    monkeypatch.setattr(settings.agent_memory,
                        "topic_negative_suppress_sim", 0.0)
    store = _stub_store()
    store.add_topic_negatives("s", [("topicX", "ALPHAQ failing prompt")])
    assert store.topic_route_suppressed("topicX", "ALPHAQ again") is False


def test_topic_positive_protects_from_suppression(monkeypatch):
    """A positive exemplar at least as close as the negatives protects the
    route — the query-local complement to the human `allowed` pin."""
    monkeypatch.setattr(settings.agent_memory,
                        "topic_negative_suppress_sim", 0.5)
    store = _stub_store()
    store.add_topic_negatives("s", [("topicX", "ALPHAQ failing prompt")])
    assert store.topic_route_suppressed("topicX", "ALPHAQ again") is True

    store.add_topic_positives("s", [("topicX", "ALPHAQ good prompt")])
    assert store.topic_route_suppressed("topicX", "ALPHAQ again") is False


def test_pass_verdict_records_topic_positive(monkeypatch):
    """A `pass`-graded banner becomes a protecting positive topic exemplar,
    gated on suppression being enabled."""
    from sqlmodel import select
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import TopicExemplar

    monkeypatch.setattr(settings.agent_memory,
                        "topic_negative_suppress_sim", 0.5)
    store = _stub_store()
    store.record_topic_injection("s-pass", "topicX", query="ALPHAQ context")
    store.apply_topic_relevance("s-pass", "pass")

    with MemorySessionLocal() as session:
        rows = session.exec(select(TopicExemplar).where(
            TopicExemplar.topic_id == "topicX",
            TopicExemplar.polarity == 1)).all()
    assert len(rows) == 1 and rows[0].source_session == "s-pass"


# ── inbox proposal notifications ──────────────────────────────

def _proposal_cards():
    from lib.agent_messages import store as messages
    return [m for m in messages.list_inbox()
            if (m.get("msg_key") or "").startswith("topic-suppress-proposal:")]


def test_notify_pushes_one_card_and_dedups():
    from lib.grader.topic_notify import notify_proposals
    _seed("topicA", ["fail", "fail", "fail"])  # over the bar → proposed
    proposed = [r for r in memory.get_store().topic_relevance_summary()
                if r["status"] == "proposed"]
    assert notify_proposals(proposed) == 1
    # a live card already exists → no re-surface on the next grade
    assert notify_proposals(proposed) == 0
    cards = _proposal_cards()
    assert len(cards) == 1
    assert cards[0]["msg_type"] == "warning"
    assert "topicA" in (cards[0]["title"] or "")


def test_resolve_dismisses_then_renotifies():
    from lib.grader.topic_notify import notify_proposals, resolve_proposal
    _seed("topicA", ["fail", "fail", "fail"])
    proposed = [r for r in memory.get_store().topic_relevance_summary()
                if r["status"] == "proposed"]
    notify_proposals(proposed)
    resolve_proposal("topicA")           # human decided → card dismissed
    assert _proposal_cards() == []
    assert notify_proposals(proposed) == 1  # still proposed → re-raised


def test_grade_pushes_proposal_to_inbox():
    # a fail grade that tips a topic over the bar surfaces an inbox card
    _seed("topicA", ["fail", "fail"])  # 2 fails so far, below min_scored=3
    memory.get_store().record_topic_injection("trX", "topicA")
    _maybe_apply_injection_relevance(
        "trX", {"injectedrelated": _grade("fail")}, _Ev(), is_test=0)
    cards = _proposal_cards()
    assert len(cards) == 1 and "topicA" in (cards[0]["title"] or "")


def test_notify_off_pushes_nothing(monkeypatch):
    monkeypatch.setattr(settings.agent_memory, "topic_relevance_notify", False)
    _seed("topicA", ["fail", "fail"])
    memory.get_store().record_topic_injection("trX", "topicA")
    _maybe_apply_injection_relevance(
        "trX", {"injectedrelated": _grade("fail")}, _Ev(), is_test=0)
    assert _proposal_cards() == []
