"""score_injection_usefulness: the deterministic inject→usefulness verdict
(engaged / ignored / no_referents), its persistence, the ordering gate,
the reflect decay rule it feeds, and the never-raise grade-wiring contract.
"""

from __future__ import annotations

import json

import lib.memory as memory
from lib.memory.feedback import score_injection_usefulness
from lib.memory.models import MemoryInput
from lib.settings import settings


# ── helpers ───────────────────────────────────────────────────

def _insert_span(trace_id, name, attrs, start_time, span_id):
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO session_spans "
            "(trace_id, span_id, name, start_time, attributes, status_code) "
            "VALUES (?, ?, ?, ?, ?, 'OK')",
            (trace_id, span_id, name, start_time, json.dumps(attrs)))
        conn.commit()
    finally:
        conn.close()


def _record_injection(session_id, memory_id, injected_at):
    """Insert one injection event with an explicit timestamp so the
    before/after-injection ordering gate can be exercised precisely."""
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import InjectionEvent
    with MemorySessionLocal() as session:
        session.add(InjectionEvent(
            session_id=session_id, memory_id=memory_id,
            injected_at=injected_at))
        session.commit()


def _remember(store, *, title, body, importance=0.5):
    return store.remember(MemoryInput(
        title=title, body=body, importance=importance, is_test=True))


def _validation_actions(store, memory_id):
    from sqlmodel import select
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import MemoryValidation
    with MemorySessionLocal() as session:
        return [a for a in session.exec(
            select(MemoryValidation.action)
            .where(MemoryValidation.memory_id == memory_id)).all()]


# ── engaged ───────────────────────────────────────────────────

def test_engaged_path_validates_and_bumps_importance():
    """A memory naming a file the session edits AFTER injection → engaged:
    a 'feedback'/'engaged' validation and a +0.05 importance bump."""
    store = memory.get_store()
    mid = _remember(store, title="Read before Edit",
                    body="Edit fails on `lib/memory/store.py` if not Read first.",
                    importance=0.5)
    _record_injection("sess-fb", mid, "2026-06-11T10:00:00")
    _insert_span("sess-fb", "tool.Edit",
                 {"file_path": "lib/memory/store.py"},
                 "2026-06-11T10:05:00", "sp-edit")

    result = score_injection_usefulness("sess-fb", store)

    assert result.engaged == 1 and result.ignored == 0
    assert mid in result.engaged_ids
    assert "engaged" in _validation_actions(store, mid)
    assert abs(store.get_dict(mid)["importance"] - 0.55) < 1e-9


def test_engaged_bump_caps_at_one():
    store = memory.get_store()
    mid = _remember(store, title="cap", body="touch `lib/foo.py` carefully",
                    importance=0.99)
    _record_injection("sess-cap", mid, "2026-06-11T10:00:00")
    _insert_span("sess-cap", "tool.Edit", {"file_path": "lib/foo.py"},
                 "2026-06-11T10:01:00", "sp-1")
    score_injection_usefulness("sess-cap", store)
    assert store.get_dict(mid)["importance"] == 1.0


# ── ignored ───────────────────────────────────────────────────

def test_ignored_path_validates_without_penalty():
    """A memory whose referents never appear in the work → ignored: a
    validation, but importance is untouched (decay is reflect's job)."""
    store = memory.get_store()
    mid = _remember(store, title="unrelated",
                    body="never touch `lib/never/referenced.py` casually",
                    importance=0.5)
    _record_injection("sess-ig", mid, "2026-06-11T10:00:00")
    _insert_span("sess-ig", "tool.Edit", {"file_path": "lib/other/thing.py"},
                 "2026-06-11T10:05:00", "sp-other")

    result = score_injection_usefulness("sess-ig", store)

    assert result.ignored == 1 and result.engaged == 0
    assert _validation_actions(store, mid) == ["ignored"]
    assert store.get_dict(mid)["importance"] == 0.5


# ── no_referents abstain ──────────────────────────────────────

def test_no_referents_abstains():
    """A memory with no concrete tokens (no paths, backticks, commands) is
    unverifiable — no signal, no validation, no importance change."""
    store = memory.get_store()
    mid = _remember(store, title="be careful",
                    body="always think before you act and communicate clearly",
                    importance=0.5)
    _record_injection("sess-nr", mid, "2026-06-11T10:00:00")
    _insert_span("sess-nr", "tool.Edit", {"file_path": "lib/anything.py"},
                 "2026-06-11T10:05:00", "sp-x")

    result = score_injection_usefulness("sess-nr", store)

    assert result.no_referents == 1
    assert result.engaged == 0 and result.ignored == 0
    assert _validation_actions(store, mid) == []


# ── idf-weighted verdict (session-span corpus) ────────────────

_COMMON = "lib/common/shared.py"     # made ubiquitous → idf→0
_RARE = "lib/rare/_find_state_evidence.py"   # one session → idf→1


def _remember_real(store, *, title, body, importance=0.5):
    """An active, *non-test* memory — its referents enter the idf vocab
    (`is_test=0`), unlike the `_remember` helper above."""
    return store.remember(MemoryInput(
        title=title, body=body, importance=importance, is_test=False))


def _build_session_corpus(store, *, n=22, common=_COMMON):
    """`n` distinct sessions, each with an injection event and a span naming
    `common` — so after a rebuild that referent saturates the session corpus
    (df≈n, idf≈0). A filler memory naming `common` puts it in the vocab."""
    filler = _remember_real(store, title="filler", body=f"see `{common}`")
    for i in range(n):
        sid = f"corpus-sess-{i}"
        _record_injection(sid, filler, "2026-06-11T09:00:00")
        _insert_span(sid, "tool.Edit", {"file_path": common},
                     "2026-06-11T09:01:00", f"{sid}-sp")


def _probe(store, monkeypatch, *, path, weight=0.5, n=22, sess="sess-probe"):
    """Build the session corpus, inject one probe memory naming `path`, fire a
    post-injection edit on `path`, rebuild the df cache, and score it."""
    from lib.memory.feedback import rebuild_session_referent_df
    monkeypatch.setattr(settings.agent_memory, "engagement_idf_min_weight", weight)
    _build_session_corpus(store, n=n)
    mid = _remember_real(store, title="probe", body=f"the bug is in `{path}`")
    _record_injection(sess, mid, "2026-06-11T10:00:00")
    _insert_span(sess, "tool.Edit", {"file_path": path},
                 "2026-06-11T10:05:00", f"{sess}-sp")
    rebuild_session_referent_df(store)
    return score_injection_usefulness(sess, store)


def test_idf_saturating_referent_match_is_ignored(monkeypatch):
    """A match on only a corpus-saturating referent (idf→0) no longer counts
    as engagement once the session corpus marks it ubiquitous — and stamps a
    *soft* ignore (engaged=0, matched=1) so the decay gate spares it."""
    store = memory.get_store()
    result = _probe(store, monkeypatch, path=_COMMON)
    assert result.ignored == 1 and result.engaged == 0
    from sqlmodel import select
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import InjectionEvent
    with MemorySessionLocal() as s:
        ev = s.exec(select(InjectionEvent)
                    .where(InjectionEvent.session_id == "sess-probe")).one()
    assert ev.engaged == 0 and ev.matched == 1   # soft ignore


def test_idf_specific_referent_match_is_engaged(monkeypatch):
    """A match on a session-specific referent (df=1, idf=1.0) clears it."""
    result = _probe(memory.get_store(), monkeypatch, path=_RARE)
    assert result.engaged == 1 and result.ignored == 0


def test_idf_zero_weight_disables_and_falls_back_to_binary(monkeypatch):
    """`engagement_idf_min_weight=0` restores the binary rule: even a
    saturating-referent match scores engaged."""
    result = _probe(memory.get_store(), monkeypatch, path=_COMMON, weight=0.0)
    assert result.engaged == 1 and result.ignored == 0


def test_idf_off_below_min_corpus(monkeypatch):
    """With idf enabled but the session corpus below `_IDF_MIN_CORPUS`, the
    cached df reports too few sessions → binary; saturating match → engaged."""
    result = _probe(memory.get_store(), monkeypatch, path=_COMMON, n=5)
    assert result.engaged == 1 and result.ignored == 0


# ── ordering gate ─────────────────────────────────────────────

def test_referent_before_injection_does_not_count():
    """A referent appearing in a span that fired BEFORE the injection
    moment can't have been guided by it → ignored, not engaged."""
    store = memory.get_store()
    mid = _remember(store, title="late",
                    body="edit `lib/memory/feedback.py` last",
                    importance=0.5)
    # Span fires at 09:00, injection only at 10:00 — pre-injection.
    _insert_span("sess-ord", "tool.Edit",
                 {"file_path": "lib/memory/feedback.py"},
                 "2026-06-11T09:00:00", "sp-early")
    _record_injection("sess-ord", mid, "2026-06-11T10:00:00")

    result = score_injection_usefulness("sess-ord", store)

    assert result.ignored == 1 and result.engaged == 0


# ── reflect decay rule ────────────────────────────────────────

def test_reflect_decays_chronically_ignored():
    """A memory with >=5 'ignored' validations, no positive signal, and
    recall_count==0 loses 0.1 importance per reflect run (floor 0.1)."""
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="ignored a lot",
                    body="touches `lib/decay/target.py` somewhere",
                    importance=0.6)
    # Make it episodic so reflect's decay pass (which walks episodic) sees
    # it, and stamp five 'ignored' validations.
    store.update(mid, tier="episodic")
    for _ in range(5):
        store.record_validation(mid, validator="feedback", action="ignored")

    reflect(store)

    assert abs(store.get_dict(mid)["importance"] - 0.5) < 1e-9


def _inject_scored(memory_id, engaged, ignored, soft=0):
    """Record already-scored injection events for a memory, each in its own
    session: `engaged` (engaged=1, matched=1), `ignored` HARD ignores
    (engaged=0, matched=0 — referents never appeared), and `soft` ignores
    (engaged=0, matched=1 — generic contact, no idf credit). Feeds the
    `engagement_match_counts` the rate-aware decay reads."""
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import InjectionEvent
    with MemorySessionLocal() as session:
        for i in range(engaged + ignored + soft):
            if i < engaged:
                e, m = 1, 1
            elif i < engaged + ignored:
                e, m = 0, 0     # hard ignore
            else:
                e, m = 0, 1     # soft ignore
            session.add(InjectionEvent(
                session_id=f"sess-eng-{memory_id[:6]}-{i}", memory_id=memory_id,
                injected_at="2026-01-01T00:00:00",
                scored_at="2026-01-01T02:00:00", engaged=e, matched=m))
        session.commit()


def test_reflect_spares_high_engagement_rate():
    """The positive half made rate-based: a memory engaged in a clear
    majority of its (densely scored) injects is spared from decay even though
    it was injected past the volume threshold and never reinforced — the case
    the old binary, grade-time-only signal silently let decay."""
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="proven useful",
                    body="touches `lib/mixed/target.py`", importance=0.6)
    store.update(mid, tier="episodic")
    _inject_scored(mid, engaged=7, ignored=2)   # 78% — well above spare rate

    reflect(store)

    assert store.get_dict(mid)["importance"] == 0.6


def test_reflect_decays_low_engagement_rate():
    """The symmetric guard: a high-volume memory engaged in only a small
    fraction of its injects decays via `low_engagement` — densifying the
    signal must not make a chronically-ignored row un-decayable just because
    it engaged once."""
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="mostly noise",
                    body="touches `lib/noise/target.py`", importance=0.6)
    store.update(mid, tier="episodic")
    _inject_scored(mid, engaged=2, ignored=18)   # 10% — well below spare rate

    reflect(store)

    assert abs(store.get_dict(mid)["importance"] - 0.5) < 1e-9
    assert "decayed_low_engagement" in _validation_actions(store, mid)


def test_reflect_spares_soft_ignored_memory():
    """The idf-hardening: a memory whose referents matched downstream only
    *generically* (soft ignores — engaged=0, matched=1) is NOT decayed, even
    at high volume. Its real value may leave no idf-specific referent; absence
    of specific evidence is not evidence of uselessness, so soft ignores stay
    out of the decay rate (decisive = engaged + hard = 0 here)."""
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="useful but generic",
                    body="config lives in `lib/quiet/generic.py`", importance=0.6)
    store.update(mid, tier="episodic")
    _inject_scored(mid, engaged=0, ignored=0, soft=15)   # all generic contact

    reflect(store)

    assert store.get_dict(mid)["importance"] == 0.6
    assert "decayed_low_engagement" not in _validation_actions(store, mid)


def test_reflect_decays_hard_not_soft_dominated():
    """Soft ignores don't rescue a memory that is mostly *hard*-ignored: with
    2 engaged, 3 soft, 18 hard the decisive rate is 2/20 = 10% < spare → decay.
    Soft contact is neutral, not a veto on a genuinely mistargeted memory."""
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="mistargeted",
                    body="touches `lib/hard/target.py`", importance=0.6)
    store.update(mid, tier="episodic")
    _inject_scored(mid, engaged=2, ignored=18, soft=3)

    reflect(store)

    assert abs(store.get_dict(mid)["importance"] - 0.5) < 1e-9
    assert "decayed_low_engagement" in _validation_actions(store, mid)


def test_low_engagement_decay_fires_once_not_every_run():
    """`low_engagement` is gated once per memory (like the injected trigger):
    a second reflect over the same static engagement evidence is a no-op."""
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="noise, decays once",
                    body="touches `lib/noise/once.py`", importance=0.6)
    store.update(mid, tier="episodic")
    _inject_scored(mid, engaged=1, ignored=19)

    reflect(store)
    after_first = store.get_dict(mid)["importance"]
    reflect(store)

    assert abs(after_first - 0.5) < 1e-9
    assert store.get_dict(mid)["importance"] == after_first


def test_reflect_decay_floors_at_baseline():
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="floored",
                    body="touches `lib/floor/target.py`", importance=0.15)
    store.update(mid, tier="episodic")
    for _ in range(5):
        store.record_validation(mid, validator="feedback", action="ignored")
    reflect(store)
    assert store.get_dict(mid)["importance"] == 0.1


def _inject_n(memory_id, n, *, reinforced=0):
    """Record `n` injection events for `memory_id`, each in its own session
    (the table PK is (session_id, memory_id)); the first `reinforced` of them
    carry a `reinforced_at` stamp."""
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import InjectionEvent
    with MemorySessionLocal() as session:
        for i in range(n):
            session.add(InjectionEvent(
                session_id=f"sess-{memory_id[:6]}-{i}", memory_id=memory_id,
                injected_at="2026-01-01T00:00:00",
                reinforced_at="2026-01-01T01:00:00" if i < reinforced else None))
        session.commit()


def _injected_threshold():
    from lib.settings import settings
    return settings.agent_memory.decay_injected_threshold


def test_reflect_decays_chronically_injected_without_ignored_validations():
    """The always-on half: a memory injected `decay_injected_threshold`+
    times, never reinforced/recalled, and with NO 'ignored' validations (none
    are ever written outside grade time) still decays. This is the case the
    old gate missed entirely."""
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="injected a lot, never reinforced",
                    body="some body", importance=0.6)
    store.update(mid, tier="episodic")
    _inject_n(mid, _injected_threshold())

    reflect(store)

    assert abs(store.get_dict(mid)["importance"] - 0.5) < 1e-9


def test_reflect_spares_reinforced_injection_from_decay():
    """A single reinforcement is a positive signal — a heavily-injected memory
    that was reinforced even once is spared."""
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="injected and reinforced",
                    body="some body", importance=0.6)
    store.update(mid, tier="episodic")
    _inject_n(mid, _injected_threshold(), reinforced=1)

    reflect(store)

    assert store.get_dict(mid)["importance"] == 0.6


def test_reflect_spares_lightly_injected_memory():
    """Below the injection threshold there isn't enough evidence to decay."""
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="barely injected",
                    body="some body", importance=0.6)
    store.update(mid, tier="episodic")
    _inject_n(mid, _injected_threshold() - 1)

    reflect(store)

    assert store.get_dict(mid)["importance"] == 0.6


def test_injected_decay_fires_once_not_every_run():
    """The injection-volume trigger reads the never-trimmed `injection_events`,
    so without a guard it would re-decay the same memory on every reflect run
    (decay coupled to run cadence, not evidence). It must fire once: a second
    run over the same static evidence is a no-op."""
    from lib.memory.reflect import reflect
    store = memory.get_store()
    mid = _remember(store, title="injected, decays once",
                    body="some body", importance=0.6)
    store.update(mid, tier="episodic")
    _inject_n(mid, _injected_threshold())

    reflect(store)
    after_first = store.get_dict(mid)["importance"]
    reflect(store)
    after_second = store.get_dict(mid)["importance"]

    assert abs(after_first - 0.5) < 1e-9
    assert after_second == after_first  # no further decay on static evidence
    assert _validation_actions(store, mid).count("decayed_injected") == 1


def test_decay_injected_threshold_zero_disables_the_half(monkeypatch):
    """Setting `decay_injected_threshold` to 0 turns off the injection-volume
    trigger entirely, even for a heavily-injected, never-reinforced row."""
    from lib.memory.reflect import reflect
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_memory, "decay_injected_threshold", 0)
    store = memory.get_store()
    mid = _remember(store, title="injected but trigger disabled",
                    body="some body", importance=0.6)
    store.update(mid, tier="episodic")
    _inject_n(mid, 20)

    reflect(store)

    assert store.get_dict(mid)["importance"] == 0.6


# ── never-raise contract ──────────────────────────────────────

def test_no_injection_events_is_a_clean_noop():
    """A session into which nothing was injected scores cleanly — empty
    result, no error."""
    store = memory.get_store()
    result = score_injection_usefulness("sess-empty", store)
    assert (result.engaged, result.ignored, result.no_referents) == (0, 0, 0)


def test_grade_wiring_never_raises(monkeypatch):
    """`_maybe_score_injection_usefulness` swallows any feedback error so a
    feedback bug can never fail a grading run."""
    from lib.grader import service

    class _Evidence:
        session = {"is_test": 0}

    def _boom(*a, **k):
        raise RuntimeError("feedback exploded")

    monkeypatch.setattr("lib.memory.feedback.score_injection_usefulness",
                        _boom)
    # Must not raise.
    service._maybe_score_injection_usefulness("sess-x", _Evidence(), 0)


# ── event stamping + engagement_counts ────────────────────────

def _event(store, session_id, memory_id):
    from sqlmodel import select
    from lib.memory.engine import MemorySessionLocal
    from lib.memory.models import InjectionEvent
    with MemorySessionLocal() as session:
        return session.exec(
            select(InjectionEvent)
            .where(InjectionEvent.session_id == session_id,
                   InjectionEvent.memory_id == memory_id)).one()


def test_verdicts_stamp_the_event_row_and_feed_engagement_counts():
    """Each verdict is written onto the (uncapped) injection event: engaged→1,
    ignored→0, abstain→NULL; all get `scored_at`. `engagement_counts` then
    reads accurate per-memory tallies straight off the event log."""
    store = memory.get_store()
    eng = _remember(store, title="eng", body="edit `lib/a/eng.py`")
    ign = _remember(store, title="ign", body="edit `lib/a/ign.py`")
    _record_injection("sess-stamp", eng, "2026-06-11T10:00:00")
    _record_injection("sess-stamp", ign, "2026-06-11T10:00:00")
    _insert_span("sess-stamp", "tool.Edit", {"file_path": "lib/a/eng.py"},
                 "2026-06-11T10:05:00", "sp-eng")

    score_injection_usefulness("sess-stamp", store)

    assert _event(store, "sess-stamp", eng).engaged == 1
    assert _event(store, "sess-stamp", ign).engaged == 0
    assert _event(store, "sess-stamp", eng).scored_at is not None
    assert store.engagement_counts()[eng] == (1, 0)
    assert store.engagement_counts()[ign] == (0, 1)
    # `matched` distinguishes the hard ignore (ign matched nothing) from an
    # engaged match, feeding the three-way split the decay gate reads.
    assert _event(store, "sess-stamp", eng).matched == 1
    assert _event(store, "sess-stamp", ign).matched == 0
    assert store.engagement_match_counts()[eng] == (1, 0, 0)
    assert store.engagement_match_counts()[ign] == (0, 0, 1)


def test_reward_importance_false_records_signal_without_bumping():
    """The bulk-sweep mode: an engaged verdict still stamps the event and
    records the validation, but leaves importance untouched, so densifying
    historical scoring can't inflate the importance axis."""
    store = memory.get_store()
    mid = _remember(store, title="x", body="edit `lib/a/keep.py`",
                    importance=0.5)
    _record_injection("sess-noimp", mid, "2026-06-11T10:00:00")
    _insert_span("sess-noimp", "tool.Edit", {"file_path": "lib/a/keep.py"},
                 "2026-06-11T10:05:00", "sp-1")

    score_injection_usefulness("sess-noimp", store, reward_importance=False)

    assert _event(store, "sess-noimp", mid).engaged == 1
    assert store.get_dict(mid)["importance"] == 0.5      # not bumped
    assert store.engagement_counts()[mid] == (1, 0)


def test_scoring_is_idempotent_per_event():
    """A second pass over a session whose events are already scored is a
    no-op — `scored_at` gates re-judging, so importance isn't double-bumped
    and counts don't double."""
    store = memory.get_store()
    mid = _remember(store, title="x", body="edit `lib/a/once.py`",
                    importance=0.5)
    _record_injection("sess-idem", mid, "2026-06-11T10:00:00")
    _insert_span("sess-idem", "tool.Edit", {"file_path": "lib/a/once.py"},
                 "2026-06-11T10:05:00", "sp-1")

    score_injection_usefulness("sess-idem", store)
    first = store.get_dict(mid)["importance"]
    second_result = score_injection_usefulness("sess-idem", store)

    assert first == 0.55
    assert (second_result.engaged, second_result.ignored) == (0, 0)
    assert store.get_dict(mid)["importance"] == first
    assert store.engagement_counts()[mid] == (1, 0)


# ── pending sweep (densification) ─────────────────────────────

def test_score_pending_sessions_scores_finished_unscored_only():
    """The sweep stamps every finished, unscored session (validation-only),
    densifying the signal beyond the rare graded session — but skips events
    younger than the lag, whose post-injection spans may not have landed."""
    from lib.memory.feedback import score_pending_sessions
    from datetime import datetime, timedelta
    store = memory.get_store()
    mid = _remember(store, title="x", body="edit `lib/a/sweep.py`",
                    importance=0.5)
    old = (datetime.now() - timedelta(hours=5)).isoformat()
    old_span = (datetime.now() - timedelta(hours=4)).isoformat()  # after inject
    fresh = datetime.now().isoformat()
    _record_injection("sess-old", mid, old)
    _record_injection("sess-fresh", mid, fresh)
    _insert_span("sess-old", "tool.Edit", {"file_path": "lib/a/sweep.py"},
                 old_span, "sp-old")

    agg = score_pending_sessions(store, lag_minutes=120)

    assert agg.engaged == 1                                  # old session scored
    assert _event(store, "sess-old", mid).engaged == 1
    assert _event(store, "sess-fresh", mid).scored_at is None  # too fresh
    assert store.get_dict(mid)["importance"] == 0.5         # validation-only
