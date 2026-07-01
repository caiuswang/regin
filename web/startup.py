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
                -- Capture source: 'hook' (live hook events) or 'transcript'
                -- (the transcript scan). See lib/trace/merge.py. Mirrors
                -- db/schema.sql; keep the two in step.
                source          TEXT NOT NULL DEFAULT 'hook',
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
    elif 'source' not in _column_names(conn, 'session_spans'):
        # Additive repair for DBs created before the append-only capture
        # split (migration 0002) added the source discriminator.
        conn.execute(
            "ALTER TABLE session_spans ADD COLUMN source TEXT NOT NULL "
            "DEFAULT 'hook'"
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
                origin        TEXT DEFAULT 'session',
                model         TEXT,
                cwd           TEXT,
                input_tokens          INTEGER,
                output_tokens         INTEGER,
                cache_read_tokens     INTEGER,
                cache_creation_tokens INTEGER,
                peak_context_tokens   INTEGER,
                peak_main_context_tokens INTEGER,
                live_context_tokens   INTEGER,
                context_window_tokens INTEGER,
                cost_usd              REAL,
                active_work_ms        INTEGER,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX idx_sessions_last_seen ON sessions(last_seen DESC)")
        conn.execute("CREATE INDEX idx_sessions_title_nocase ON sessions(title COLLATE NOCASE)")
    else:
        # Additive repairs for DBs created before later columns landed.
        cols = _column_names(conn, 'sessions')
        if 'cwd' not in cols:  # repo-filter feature
            conn.execute("ALTER TABLE sessions ADD COLUMN cwd TEXT")
        if 'origin' not in cols:  # sessions.origin axis (migration 0002)
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN origin TEXT DEFAULT 'session'"
            )
        if 'live_context_tokens' not in cols:  # segment-aware ctx% (post-/compact)
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN live_context_tokens INTEGER"
            )
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


def init_session_grades_schema(conn) -> None:
    """Create `session_grades` (post-hoc rubric grades) if missing."""
    if not _table_exists(conn, "session_grades"):
        conn.execute("""
            CREATE TABLE session_grades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id        TEXT NOT NULL,
                axis            TEXT NOT NULL,
                verdict         TEXT NOT NULL,
                tier            TEXT NOT NULL DEFAULT 'screen',
                scoreboard      TEXT NOT NULL DEFAULT '{}',
                report          TEXT NOT NULL DEFAULT '',
                detail          TEXT NOT NULL DEFAULT '{}',
                rubric_version  TEXT,
                judge           TEXT,
                is_test         INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX idx_session_grades_trace ON session_grades(trace_id, axis)"
        )
        conn.execute(
            "CREATE INDEX idx_session_grades_created ON session_grades(created_at DESC)"
        )
    conn.commit()


def init_pattern_deployments_schema(conn) -> None:
    """Bring an older `pattern_deployments` table up to the multi-provider
    shape if it predates the `provider` column.

    `regin init` builds the current schema from db/schema.sql, but an install
    upgraded in place never re-runs it and nothing auto-runs the alembic 0004
    migration. Without the `provider` column every skill push/undeploy/backfill
    — which all read and write it — raises "no such column: provider". Rebuild
    the table exactly as migration 0004 does: add the column AND the
    provider-aware UNIQUE (`record_deployment` needs the latter so the same
    skill can deploy to two providers in one project — the old
    UNIQUE(pattern_slug, scope, project_id) would block the second), then
    backfill each legacy row's owning provider from its on-disk path.

    Idempotent: a table that already has the column is left untouched, and a
    fresh install (column already present from schema.sql) skips the rebuild.
    """
    if not _table_exists(conn, 'pattern_deployments'):
        return
    if 'provider' in _column_names(conn, 'pattern_deployments'):
        return
    conn.executescript("""
        CREATE TABLE pattern_deployments_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_slug    TEXT NOT NULL,
            scope           TEXT NOT NULL,
            project_id      INTEGER,
            provider        TEXT,
            deployed_path   TEXT NOT NULL,
            deployed_at     TEXT NOT NULL DEFAULT (datetime('now')),
            deployed_by     INTEGER,
            UNIQUE(pattern_slug, scope, project_id, provider)
        );
        INSERT INTO pattern_deployments_new
            (id, pattern_slug, scope, project_id, provider,
             deployed_path, deployed_at, deployed_by)
        SELECT id, pattern_slug, scope, project_id, NULL,
               deployed_path, deployed_at, deployed_by
        FROM pattern_deployments;
        DROP TABLE pattern_deployments;
        ALTER TABLE pattern_deployments_new RENAME TO pattern_deployments;
        UPDATE pattern_deployments SET provider = CASE
            WHEN deployed_path LIKE '%/.codex/%'     THEN 'codex'
            WHEN deployed_path LIKE '%/.kimi-code/%' THEN 'kimi'
            WHEN deployed_path LIKE '%/.agent/%'     THEN 'generic'
            ELSE 'claude'
        END
        WHERE provider IS NULL;
        CREATE INDEX IF NOT EXISTS idx_pattern_deployments_pattern
            ON pattern_deployments(pattern_slug);
        CREATE INDEX IF NOT EXISTS idx_pattern_deployments_project
            ON pattern_deployments(project_id);
        CREATE INDEX IF NOT EXISTS idx_pattern_deployments_scope
            ON pattern_deployments(scope);
    """)
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
        TopicRefDigest,
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
            TopicRefDigest.__table__,
        ],
    )

    # Per-topic wiki column (added after these tables shipped): create_all
    # never alters an existing table, so ADD it explicitly on older DBs.
    for _topic_table in ("proposal_topics", "proposal_revision_topics"):
        if _table_exists(conn, _topic_table) and "wiki_md" not in _column_names(conn, _topic_table):
            conn.execute(
                f"ALTER TABLE {_topic_table} ADD COLUMN wiki_md TEXT NOT NULL DEFAULT ''"
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
