"""`sessions.is_test` vs the span scan it replaced.

The original equivalence check for this refactor was run against the live DB
*after* its test sessions had been purged — zero test-marked rows on either
side, so every comparison agreed trivially and proved nothing. These tests
build a corpus that actually exercises the marker: real, test-marked, orphan
(no sessions row), and the NULL primary key SQLite permits.
"""

from __future__ import annotations

import sqlite3

import pytest

import lib.orm.engine as _engine
from lib.trace.trace_service.queries import _TEST_EXCLUSION


OLD_EXCLUSION = (
    "{col} NOT IN (SELECT DISTINCT trace_id FROM session_spans "
    "WHERE json_extract(attributes, '$.is_test') = 1)"
)


@pytest.fixture
def corpus():
    """real / test-marked / orphan sessions, each with one skill_read."""
    conn = sqlite3.connect(_engine.DB_PATH)
    conn.row_factory = sqlite3.Row
    for trace_id, is_test in (("real", 0), ("testy", 1)):
        conn.execute(
            "INSERT INTO sessions (trace_id, is_test, started_at, last_seen) "
            "VALUES (?,?,?,?)", (trace_id, is_test, "2026-01-01", "2026-01-01"))
        conn.execute(
            "INSERT INTO session_spans "
            "(trace_id, span_id, name, attributes, start_time, status_code) "
            "VALUES (?,?,?,?,?,?)",
            (trace_id, f"sp-{trace_id}", "tool.Bash",
             '{"is_test": true}' if is_test else "{}", "2026-01-01", "OK"))
    # An orphan: skill_reads row whose session has no `sessions` row at all.
    for trace_id in ("real", "testy", "orphan"):
        conn.execute(
            "INSERT INTO skill_reads (skill_id, session_id, file_path, read_at) "
            "VALUES (?,?,?,?)", ("alpha", trace_id, "s.md", "2026-01-01T00:00:00"))
    conn.commit()
    yield conn
    conn.close()


def _ids(conn, exclusion: str) -> list[str]:
    sql = f"SELECT session_id FROM skill_reads WHERE {exclusion.format(col='session_id')}"
    return sorted(r[0] for r in conn.execute(sql).fetchall())


def test_new_exclusion_matches_the_span_scan_on_a_discriminating_corpus(corpus):
    """The corpus contains a genuinely test-marked session, so agreement
    here is real agreement — not two empty sets matching."""
    old, new = _ids(corpus, OLD_EXCLUSION), _ids(corpus, _TEST_EXCLUSION)
    assert "testy" not in new, "test session must be excluded"
    assert old == new == ["orphan", "real"]


def test_orphan_sessions_survive_both_forms(corpus):
    """A skill_read whose session_id has no `sessions` row must still show —
    `NOT IN` over a set that lacks the value is TRUE, not NULL."""
    assert "orphan" in _ids(corpus, _TEST_EXCLUSION)
    assert "orphan" in _ids(corpus, OLD_EXCLUSION)


def test_current_schema_forbids_a_null_trace_id(corpus):
    """`db/schema.sql` declares `trace_id TEXT PRIMARY KEY NOT NULL`, so a
    freshly-initialised DB cannot hit the NULL trap at all."""
    with pytest.raises(sqlite3.IntegrityError):
        corpus.execute(
            "INSERT INTO sessions (trace_id, is_test, started_at, last_seen) "
            "VALUES (NULL, 1, '2026-01-01', '2026-01-01')")


def test_null_trace_id_does_not_blank_the_feed_on_a_legacy_schema(tmp_path):
    """Databases created before `NOT NULL` was added to `sessions.trace_id`
    still have a bare `TEXT PRIMARY KEY`, which SQLite lets hold NULL — the
    developer's own db/regin.db is one of them. One such row makes the
    sub-select emit NULL, `NOT IN` evaluate to NULL for every row, and the
    skill-reads / mcp-calls / plans feeds silently empty.
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE sessions (trace_id TEXT PRIMARY KEY, is_test INTEGER DEFAULT 0);
        CREATE TABLE skill_reads (session_id TEXT);
        INSERT INTO sessions VALUES ('real', 0), ('testy', 1), (NULL, 1);
        INSERT INTO skill_reads VALUES ('real'), ('testy'), ('orphan');
    """)
    conn.commit()

    def ids(exclusion):
        sql = ("SELECT session_id FROM skill_reads WHERE "
               + exclusion.format(col="session_id"))
        return sorted(r[0] for r in conn.execute(sql).fetchall())

    naive = "{col} NOT IN (SELECT trace_id FROM sessions WHERE is_test = 1)"
    assert ids(naive) == [], "confirms the trap is real on a legacy schema"
    assert ids(_TEST_EXCLUSION) == ["orphan", "real"], "the guard holds"
    conn.close()
