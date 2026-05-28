"""Boot-time schema bootstrap.

`web.app.create_app()` calls these before any route is registered so that
a Flask process started against an older local DB still has the tables,
indexes, and additive columns the web surface expects.

Most helpers remain pure CREATE TABLE / CREATE INDEX guards. Topic
proposal bootstrapping is the one exception: that feature has been
evolving rapidly, so installs may legitimately have older table shapes
that need additive ALTER TABLE repairs to avoid 500s in the workspace.
"""

from __future__ import annotations


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone())


def _column_names(conn, table: str) -> set[str]:
    return {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def init_session_spans_schema(conn) -> None:
    """Create `session_spans` plus its indexes if missing."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_spans'"
    ).fetchone()
    if not exists:
        conn.execute("""
            CREATE TABLE session_spans (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id        TEXT NOT NULL,
                span_id         TEXT NOT NULL,
                parent_id       TEXT,
                name            TEXT NOT NULL,
                kind            TEXT DEFAULT 'internal',
                start_time      TEXT NOT NULL,
                end_time        TEXT,
                duration_ms     INTEGER,
                attributes      TEXT NOT NULL DEFAULT '{}',
                status_code     TEXT DEFAULT 'UNSET',
                status_message  TEXT,
                output_tokens   INTEGER,
                input_tokens    INTEGER,
                image_tokens    INTEGER,
                cost_usd        REAL,
                tool_use_id     TEXT,
                turn_uuid       TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX idx_session_spans_trace ON session_spans(trace_id)")
        conn.execute("CREATE INDEX idx_session_spans_start ON session_spans(start_time)")
        conn.execute("CREATE INDEX idx_session_spans_name ON session_spans(name)")
        conn.execute("CREATE INDEX idx_session_spans_parent ON session_spans(parent_id)")
        conn.execute(
            "CREATE INDEX idx_session_spans_tool_use_id ON session_spans(tool_use_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX ux_session_spans_trace_span "
            "ON session_spans(trace_id, span_id)"
        )
    conn.commit()


def init_sessions_schema(conn) -> None:
    """Create the `sessions` table if missing.

    The table holds per-session counters + title that the Sessions list
    view previously computed via GROUP BY on every request. Counters are
    maintained incrementally by the ingest path.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'"
    ).fetchone()
    if not exists:
        conn.execute("""
            CREATE TABLE sessions (
                trace_id      TEXT PRIMARY KEY,
                title         TEXT,
                title_source  TEXT,
                status        TEXT,
                last_start_at TEXT,
                ended_at      TEXT,
                ended_reason  TEXT,
                started_at    TEXT NOT NULL,
                last_seen     TEXT NOT NULL,
                span_count    INTEGER NOT NULL DEFAULT 0,
                skill_reads   INTEGER NOT NULL DEFAULT 0,
                file_edits    INTEGER NOT NULL DEFAULT 0,
                rule_checks   INTEGER NOT NULL DEFAULT 0,
                plan_enters   INTEGER NOT NULL DEFAULT 0,
                prompts       INTEGER NOT NULL DEFAULT 0,
                tool_calls    INTEGER NOT NULL DEFAULT 0,
                is_test       INTEGER NOT NULL DEFAULT 0,
                test_name     TEXT,
                agent_type    TEXT,
                model         TEXT,
                cwd           TEXT,
                input_tokens          INTEGER,
                output_tokens         INTEGER,
                cache_read_tokens     INTEGER,
                cache_creation_tokens INTEGER,
                peak_context_tokens   INTEGER,
                peak_main_context_tokens INTEGER,
                context_window_tokens INTEGER,
                cost_usd              REAL,
                active_work_ms        INTEGER,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX idx_sessions_last_seen ON sessions(last_seen DESC)")
        conn.execute("CREATE INDEX idx_sessions_title_nocase ON sessions(title COLLATE NOCASE)")
    elif 'cwd' not in _column_names(conn, 'sessions'):
        # Additive repair for DBs created before the repo-filter feature.
        conn.execute("ALTER TABLE sessions ADD COLUMN cwd TEXT")
    conn.commit()


def init_session_repos_schema(conn) -> None:
    """Create the `session_repos` join table if missing.

    One row per (trace_id, repo_id) — the set of registered repos a
    session touched. `is_primary=1` marks the repo the session started
    in (its `session.start` cwd). Populated at ingest time and by the
    `regin sessions resolve-repos` backfill.
    """
    if not _table_exists(conn, 'session_repos'):
        conn.execute("""
            CREATE TABLE session_repos (
                trace_id   TEXT NOT NULL,
                repo_id    INTEGER NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (trace_id, repo_id)
            )
        """)
        conn.execute("CREATE INDEX idx_session_repos_repo ON session_repos(repo_id)")
    conn.commit()


def init_turn_usage_schema(conn) -> None:
    """Create `turn_usage` if missing.

    Per-assistant-turn token counters, keyed on the transcript message
    uuid so handler replays are idempotent.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turn_usage'"
    ).fetchone()
    if not exists:
        conn.execute("""
            CREATE TABLE turn_usage (
                trace_id               TEXT NOT NULL,
                turn_uuid              TEXT NOT NULL,
                turn_index             INTEGER NOT NULL,
                timestamp              TEXT NOT NULL,
                model                  TEXT,
                input_tokens           INTEGER NOT NULL DEFAULT 0,
                output_tokens         INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens      INTEGER NOT NULL DEFAULT 0,
                cache_creation_tokens  INTEGER NOT NULL DEFAULT 0,
                context_used_tokens    INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens       INTEGER,
                cost_usd               REAL,
                effort_level           TEXT,
                request_id             TEXT,
                created_at             TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (trace_id, turn_uuid)
            )
        """)
        conn.execute(
            "CREATE INDEX idx_turn_usage_trace_ts ON turn_usage(trace_id, timestamp)"
        )
    conn.commit()


def init_prompt_images_schema(conn) -> None:
    """Create `prompt_images` if missing.

    Holds user-submitted images attached to `prompt` spans (one row per
    image in submission order). Bytes are stored decoded as a BLOB; the
    UI fetches them through `/api/sessions/<trace_id>/prompts/<span_id>/images/<idx>`.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prompt_images'"
    ).fetchone()
    if not exists:
        conn.execute("""
            CREATE TABLE prompt_images (
                trace_id        TEXT NOT NULL,
                prompt_span_id  TEXT NOT NULL,
                idx             INTEGER NOT NULL,
                media_type      TEXT NOT NULL,
                bytes           BLOB NOT NULL,
                byte_size       INTEGER NOT NULL,
                sha256          TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (trace_id, prompt_span_id, idx)
            )
        """)
        conn.execute(
            "CREATE INDEX idx_prompt_images_trace ON prompt_images(trace_id)"
        )
    conn.commit()


def init_topic_proposal_schema(conn) -> None:
    """Create/repair the topic proposal tables used by the Topics workspace."""
    from lib.orm.base import Base
    from lib.orm.engine import get_engine
    from lib.orm.models import (
        GraphSnapshot,
        ProposalFeedbackComment,
        ProposalFeedbackThread,
        ProposalRevision,
        ProposalRevisionTopic,
        ProposalRun,
        ProposalTopic,
        TopicAudit,
    )

    Base.metadata.create_all(
        get_engine(),
        tables=[
            ProposalRun.__table__,
            ProposalTopic.__table__,
            ProposalRevision.__table__,
            ProposalRevisionTopic.__table__,
            ProposalFeedbackThread.__table__,
            ProposalFeedbackComment.__table__,
            GraphSnapshot.__table__,
            TopicAudit.__table__,
        ],
    )

    if _table_exists(conn, "proposal_feedback_threads"):
        columns = _column_names(conn, "proposal_feedback_threads")
        if "revision_id" not in columns:
            conn.execute("ALTER TABLE proposal_feedback_threads ADD COLUMN revision_id INTEGER")
        if "addressed_in_revision_id" not in columns:
            conn.execute(
                "ALTER TABLE proposal_feedback_threads ADD COLUMN addressed_in_revision_id INTEGER"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_proposal_feedback_threads_revision_id "
            "ON proposal_feedback_threads(revision_id)"
        )
        conn.execute(
            """
            UPDATE proposal_feedback_threads
            SET revision_id = (
                SELECT pr.id
                FROM proposal_revisions pr
                WHERE pr.run_id = proposal_feedback_threads.run_id
                ORDER BY pr.revision_number DESC, pr.id DESC
                LIMIT 1
            )
            WHERE revision_id IS NULL
              AND EXISTS (
                SELECT 1 FROM proposal_revisions pr
                WHERE pr.run_id = proposal_feedback_threads.run_id
              )
            """
        )
        if "addressed_in_run_id" in columns:
            conn.execute(
                """
                UPDATE proposal_feedback_threads
                SET addressed_in_revision_id = (
                    SELECT pr.id
                    FROM proposal_revisions pr
                    WHERE pr.run_id = proposal_feedback_threads.run_id
                    ORDER BY pr.revision_number DESC, pr.id DESC
                    LIMIT 1
                )
                WHERE addressed_in_run_id IS NOT NULL
                  AND addressed_in_revision_id IS NULL
                  AND EXISTS (
                    SELECT 1 FROM proposal_revisions pr
                    WHERE pr.run_id = proposal_feedback_threads.run_id
                  )
                """
            )
    conn.commit()
