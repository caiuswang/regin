"""The reconciliation gate of the migration: four figures, per feature.

The whole point of a read-path swap is that it changes no answer. So the test
holds a database in its *pre-migration* shape, seeds one feature of each
origin, runs the backfill, and asserts the new derivation reports what the old
one did — feature by feature, not in aggregate, because an aggregate can agree
while two features have swapped their numbers.

The legacy shape is spelled out here rather than read from `db/schema.sql`:
step 6 of the migration deletes those tables, and a gate that dies with the
thing it guards proves nothing about the install that ran it.
"""

from __future__ import annotations

import pytest
from sqlmodel import text

from lib.features import events, migrate
from lib.orm import SessionLocal

#: The 0026 shape, restored on top of the current one — which is exactly the
#: state `0027` leaves behind: both vocabularies present, nothing dropped yet.
#: Spelled out rather than read from `db/schema.sql`, because step 6 of the
#: migration deletes these and a gate that dies with the thing it guards proves
#: nothing about the install that ran it.
_LEGACY_SHAPE = (
    "ALTER TABLE agent_tasks ADD COLUMN parent_id INTEGER",
    "ALTER TABLE agent_tasks ADD COLUMN claimed_at TEXT",
    "ALTER TABLE agent_tasks ADD COLUMN completions_walked_back"
    " INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE agent_tasks ADD COLUMN params_json TEXT",
    "ALTER TABLE agent_tasks ADD COLUMN autorun INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE defects ADD COLUMN feature_key TEXT",
    "ALTER TABLE defects ADD COLUMN blamed_against_state TEXT",
    "ALTER TABLE defects ADD COLUMN fix_provider TEXT",
    "ALTER TABLE defects ADD COLUMN fix_external_key TEXT",
    "ALTER TABLE session_features ADD COLUMN feature_key TEXT",
    "CREATE TABLE agent_task_links (id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " task_id INTEGER NOT NULL, provider TEXT NOT NULL,"
    " external_key TEXT NOT NULL, url TEXT NOT NULL, sync_error TEXT,"
    " created_at TEXT)",
    "CREATE TABLE issue_cache (provider TEXT NOT NULL,"
    " external_key TEXT NOT NULL, snapshot_json TEXT NOT NULL,"
    " synced_at TEXT NOT NULL, PRIMARY KEY (provider, external_key))",
)


@pytest.fixture
def legacy_db(tmp_db):
    """The database as 0027 left it, seeded with two features' worth of work.

    * `regin:1` — a native feature, shipped once and walked back once, with
      two sessions and one escaped defect.
    * `CAI-9` — a linked feature, one session, one defect blamed while it was
      still building.
    """
    with SessionLocal() as session:
        for statement in _LEGACY_SHAPE:
            session.exec(text(statement))
        session.exec(text(
            "INSERT INTO agent_tasks (id, title, status, source, claimed_at,"
            " completions_walked_back) VALUES"
            " (1, 'native feature', 'done', 'test', '2026-09-01 10:00:00', 1)"))
        session.exec(text(
            "INSERT INTO agent_tasks (id, title, status, source)"
            " VALUES (2, 'linked feature', 'idea', 'test')"))
        session.exec(text(
            "INSERT INTO agent_task_links (task_id, provider, external_key,"
            " url) VALUES (2, 'linear', 'CAI-9', 'https://x/CAI-9')"))
        for trace, cost in (("t-a", 1.5), ("t-b", 2.5), ("t-c", 4.0)):
            session.exec(text(
                "INSERT INTO sessions (trace_id, title, started_at, last_seen,"
                " cost_usd) VALUES (:t, :t, '2026-09-01 10:00:00',"
                " '2026-09-01 11:00:00', :c)").bindparams(t=trace, c=cost))
        for attempt, (task_id, trace) in enumerate(
                ((1, "t-a"), (1, "t-b"), (2, "t-c")), start=1):
            session.exec(text(
                "INSERT INTO agent_task_runs (task_id, trace_id, attempt,"
                " status) VALUES (:i, :t, :a, 'done')"
            ).bindparams(i=task_id, t=trace, a=attempt))
        session.exec(text(
            "INSERT INTO defects (feature_key, symptom, symptom_key,"
            " blamed_against_state) VALUES"
            " ('regin:1', 'it leaked', 'it leaked', 'completed')"))
        session.exec(text(
            "INSERT INTO defects (feature_key, symptom, symptom_key,"
            " blamed_against_state) VALUES"
            " ('CAI-9', 'found mid-build', 'found mid-build', 'started')"))
        session.commit()
    return tmp_db


def _legacy(sql: str, **binds):
    with SessionLocal() as session:
        return session.exec(text(sql).bindparams(**binds)).first()


def _before() -> dict:
    """The four figures, read the way the old code read them."""
    out = {}
    for key, task_id in (("regin:1", 1), ("CAI-9", 2)):
        sessions, cost = _legacy(
            "SELECT COUNT(DISTINCT r.trace_id),"
            " COALESCE(SUM(s.cost_usd), 0) FROM agent_task_runs r"
            " JOIN sessions s ON s.trace_id = r.trace_id"
            " WHERE r.task_id = :i", i=task_id)
        rounds = _legacy(
            "SELECT 1 + completions_walked_back FROM agent_tasks WHERE id = :i",
            i=task_id)[0]
        escapes = _legacy(
            "SELECT COUNT(*) FROM defects WHERE feature_key = :k"
            " AND blamed_against_state = 'completed'", k=key)[0]
        out[key] = {"sessions": sessions, "cost": cost, "rounds": rounds,
                    "escapes": escapes}
    return out


@pytest.fixture
def migrated(legacy_db):
    """`{legacy key: (before figures, feature id)}` once the backfill has run.

    The key→id map comes back from `backfill()` itself rather than from a
    lookup the migration leaves behind: the resolver is the one temporary
    thing this change introduces, and a gate that depended on it would keep it
    alive past the step that deletes it.
    """
    before = _before()
    mapping = migrate.backfill()["features"]
    return {key: (figures, mapping[key]) for key, figures in before.items()}


def test_sessions_match_pre_migration(migrated):
    from lib.features import ledger as ledger_module
    for _key, (before, feature_id) in migrated.items():
        assert len(ledger_module.build(feature_id).rows) == before["sessions"]


def test_cost_matches_pre_migration(migrated):
    from lib.features import ledger as ledger_module
    for _key, (before, feature_id) in migrated.items():
        assert ledger_module.build(feature_id).cost.usd == before["cost"]


def test_rounds_match_pre_migration(migrated):
    for _key, (before, feature_id) in migrated.items():
        assert events.rounds(feature_id) == before["rounds"]


def test_escapes_match_pre_migration(migrated):
    from lib.defects import escapes as escapes_module
    for _key, (before, feature_id) in migrated.items():
        assert escapes_module.counts_for(feature_id).escapes == \
            before["escapes"]
