-- regin schema

CREATE TABLE IF NOT EXISTS repos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    path            TEXT NOT NULL,
    description     TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    default_branch  TEXT NOT NULL DEFAULT 'production',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS branches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id             INTEGER NOT NULL REFERENCES repos(id),
    name                TEXT NOT NULL,
    is_tracked          INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(repo_id, name)
);

CREATE TABLE IF NOT EXISTS pattern_docs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    category        TEXT NOT NULL,
    content_hash    TEXT,
    source_kind     TEXT NOT NULL DEFAULT 'pattern',
    repo_id         INTEGER REFERENCES repos(id) ON DELETE CASCADE,
    description     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_pattern_docs_kind_repo ON pattern_docs(source_kind, repo_id);

CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    category    TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS doc_tags (
    doc_id  INTEGER NOT NULL REFERENCES pattern_docs(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (doc_id, tag_id)
);

-- Trace log of every rule check emitted by the PostToolUse grit hook.
-- `triggered = 1` when match_count > 0 (i.e. the rule fired on the file).
-- Used by the web UI (/rules/triggers) to show that rules are actually running.
-- `experiment_id` is set by the ingest handler when a concealment experiment
-- was active on the rule's guide at the moment of the check — NULL means
-- baseline (no experiment active).
CREATE TABLE IF NOT EXISTS rule_triggers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    repo            TEXT,
    match_count     INTEGER NOT NULL DEFAULT 0,
    triggered       INTEGER NOT NULL DEFAULT 0,
    severity        TEXT,
    guide           TEXT,
    summary         TEXT,
    source          TEXT,
    session_id      TEXT,
    span_id         TEXT,
    experiment_id   INTEGER,
    checked_at      TEXT NOT NULL DEFAULT (datetime('now')),
    -- Denormalized fast-filter for "is this event noise?". Kept in
    -- sync with rule_trigger_suppressions (defined below) by the
    -- suppress/unsuppress endpoints, which wrap both writes in a
    -- single transaction. Every aggregate query (fires, checks, spark,
    -- top files, KPI tiles) filters WHERE suppressed=0.
    suppressed      INTEGER NOT NULL DEFAULT 0
);

-- Per-event suppression metadata. The row's existence + the
-- rule_triggers.suppressed boolean together represent "this fire was
-- flagged as a false positive and should not count". CASCADE removes
-- the metadata when its parent event row is wiped.
CREATE TABLE IF NOT EXISTS rule_trigger_suppressions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_trigger_id         INTEGER NOT NULL UNIQUE
                              REFERENCES rule_triggers(id) ON DELETE CASCADE,
    suppressed_by_id        INTEGER NOT NULL,
    suppressed_by_username  TEXT NOT NULL,
    suppressed_at           TEXT NOT NULL DEFAULT (datetime('now')),
    reason                  TEXT
);

-- Skill content read traces: logs when Claude Code reads a skill's
-- companion content.md file via the PostToolUse Read hook.
CREATE TABLE IF NOT EXISTS skill_reads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id        TEXT NOT NULL,
    session_id      TEXT,
    file_path       TEXT NOT NULL,
    found           INTEGER NOT NULL DEFAULT 1,
    source          TEXT DEFAULT 'read',
    command_args    TEXT,
    read_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_skill_reads_skill ON skill_reads(skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_reads_session ON skill_reads(session_id);
CREATE INDEX IF NOT EXISTS idx_skill_reads_read_at ON skill_reads(read_at);

-- Plan mode session traces: best-effort link between a session and the
-- most recently modified plan file when EnterPlanMode was detected.
CREATE TABLE IF NOT EXISTS plan_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,
    plan_filename       TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    draft_completed_at  TEXT,
    review_started_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_plan_sessions_session ON plan_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_plan_sessions_plan ON plan_sessions(plan_filename);
CREATE INDEX IF NOT EXISTS idx_plan_sessions_started ON plan_sessions(started_at);

-- Agent → human message channel (the `send_to_user` inbox). Canonical,
-- mutable store written by the PostToolUse hook when an
-- `mcp__*__send_to_user` call lands — NOT reconstructed from session_spans.
-- msg_key supersedes a prior message in place (progress that resolves to
-- done); read/ack/dismiss timestamps drive the cross-session unread badge.
CREATE TABLE IF NOT EXISTS agent_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    span_id         TEXT,
    agent_id        TEXT,
    agent_type      TEXT,
    msg_type        TEXT NOT NULL DEFAULT 'progress',
    title           TEXT,
    body            TEXT NOT NULL DEFAULT '',
    msg_key         TEXT,
    links           TEXT,
    pinned          INTEGER NOT NULL DEFAULT 0,
    version         INTEGER NOT NULL DEFAULT 1,
    webhook_status  TEXT,
    read_at         TEXT,
    acked_at        TEXT,
    dismissed_at    TEXT,
    is_test         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_trace ON agent_messages(trace_id);
CREATE INDEX IF NOT EXISTS idx_agent_messages_created ON agent_messages(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_messages_unread ON agent_messages(read_at);
-- Supersede lookup: one live keyed message per (session, key).
CREATE INDEX IF NOT EXISTS idx_agent_messages_key ON agent_messages(trace_id, msg_key);

-- Agent-bridge pane registry: session → tmux pane identity triple (pane id,
-- tmux server pid, pane shell pid). Canonical, mutable store written by the
-- SessionStart hook — NOT reconstructed from session_spans. One row per
-- trace_id; a resume UPSERT overwrites all coordinates. `reachable` is the
-- per-session bridge opt-in (REGIN_BRIDGE=1 at launch); later slices may
-- flip it off without deleting the identity row.
CREATE TABLE IF NOT EXISTS bridge_panes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL UNIQUE,
    pane_id         TEXT NOT NULL,
    tmux_server_pid INTEGER NOT NULL,
    pane_pid        INTEGER NOT NULL,
    tmux_socket     TEXT,
    reachable       INTEGER NOT NULL DEFAULT 0,
    cwd             TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_bridge_panes_reachable ON bridge_panes(reachable);

-- Agent-bridge inbox: append-only rows for steering messages pushed at a
-- live session (POST /api/bridge/messages). Canonical, mutable store written
-- by the HTTP surface — one row per attempt, with a clipped sender, a
-- sanitized+capped body (ANSI/control bytes stripped, newlines flattened,
-- bounded at agent_bridge.max_text_len at insert — safe to render in the
-- inbox), and the delivery outcome (delivered flag, detail, path) stamped
-- after the tmux send. Undeliverable attempts stay recorded (delivered=0)
-- rather than vanishing, so the inbox shows what came from where.
CREATE TABLE IF NOT EXISTS bridge_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    sender          TEXT,
    delivered       INTEGER NOT NULL DEFAULT 0,
    delivery_detail TEXT,
    delivery_path   TEXT,
    is_test         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    delivered_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_bridge_messages_trace ON bridge_messages(trace_id);
CREATE INDEX IF NOT EXISTS idx_bridge_messages_created ON bridge_messages(created_at DESC);

-- Post-hoc rubric grades for captured sessions (lib/grader/). Two
-- independent axes per session — 'correctness' (claim groundedness /
-- coverage / source quality) and 'process' (tool-use, redundancy,
-- reliability, cost-proportionality) — graded separately and never fused.
-- Append-only: re-grading inserts a new row; readers take the latest row
-- per (trace_id, axis).
CREATE TABLE IF NOT EXISTS session_grades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    axis            TEXT NOT NULL,                   -- 'correctness' | 'process'
    verdict         TEXT NOT NULL,                   -- correctness: satisfied|needs_revision|fail
                                                     -- process: efficient|acceptable|wasteful
    tier            TEXT NOT NULL DEFAULT 'screen',  -- 'screen' (mechanical) | 'deep' (LLM-assisted)
    scoreboard      TEXT NOT NULL DEFAULT '{}',      -- JSON per-criterion counters/ratios
    report          TEXT NOT NULL DEFAULT '',        -- scoreboard-then-failure-bullets text
    detail          TEXT NOT NULL DEFAULT '{}',      -- JSON claim ledger / process episodes
    rubric_version  TEXT,
    judge           TEXT,                            -- 'mechanical' or external agent id
    is_test         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_session_grades_trace ON session_grades(trace_id, axis);
CREATE INDEX IF NOT EXISTS idx_session_grades_created ON session_grades(created_at DESC);

-- OpenTelemetry-inspired session spans for unified execution tracing.
-- trace_id = Claude session_id. parent_id = null for root spans.
CREATE TABLE IF NOT EXISTS session_spans (
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
    -- Owning agent for the span: NULL = the main agent, else the subagent's
    -- id. Promoted from attributes.agent_id (the JSON was the sole home) so
    -- the roster/phase reads group on an indexed column instead of
    -- json_extract-scanning every row. Stamped at ingest; the kimi subagent
    -- pass (lib/trace/kimi_subagents.py) also sets it when it tags tool spans.
    agent_id        TEXT,
    -- Issuing prompt submission: the hook envelope's `prompt_id` (Claude Code
    -- 2.1.195+), stamped by post_tool_trace onto attributes.source_prompt_id
    -- and promoted here at insert time so the serve-time ladder can value-join
    -- a tool span to its `prompt-<uuid>` anchor. The value stays in attributes
    -- too; readers fall back to it for rows written before this promotion.
    source_prompt_id TEXT,
    -- Which capture source wrote this row: 'hook' (live hook events —
    -- tool timing, permissions, skill reads, the in-flight prompt
    -- placeholder) or 'transcript' (the transcript scan — prompt anchors,
    -- assistant_response/thinking, local commands). The store is
    -- append-only; both sources coexist and the serve-time merge
    -- (lib/trace/merge.py) selects winners. Defaults to 'hook'.
    source          TEXT NOT NULL DEFAULT 'hook',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_session_spans_trace ON session_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_session_spans_start ON session_spans(start_time);
CREATE INDEX IF NOT EXISTS idx_session_spans_name ON session_spans(name);
CREATE INDEX IF NOT EXISTS idx_session_spans_parent ON session_spans(parent_id);
CREATE INDEX IF NOT EXISTS idx_session_spans_tool_use_id ON session_spans(tool_use_id);
CREATE INDEX IF NOT EXISTS idx_session_spans_trace_agent ON session_spans(trace_id, agent_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_session_spans_trace_span ON session_spans(trace_id, span_id);

-- Per-session metadata, maintained incrementally at ingest time so the
-- Sessions list view doesn't have to GROUP BY + aggregate spans on every
-- render. Counters stay in sync because we only bump them for spans that
-- are newly inserted (dedup is detected in the ingest path).
CREATE TABLE IF NOT EXISTS sessions (
    trace_id      TEXT PRIMARY KEY NOT NULL,
    title         TEXT,
    title_source  TEXT,                -- 'first_prompt' | 'user' | NULL
    status        TEXT,                -- 'active' | 'ended' | NULL (legacy)
    last_start_at TEXT,                -- max start_time of any session.start span
    ended_at      TEXT,                -- max start_time of any session.end span
    ended_reason  TEXT,                -- from most recent session.end span attributes.reason
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
    agent_type    TEXT,                -- vendor of the launching agent: 'claude' | 'codex' | NULL
    origin        TEXT DEFAULT 'session',  -- what produced this row: 'session' (interactive) | 'workflow' (captured run) | future kinds
    model         TEXT,
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    cache_read_tokens     INTEGER,
    cache_creation_tokens INTEGER,
    peak_context_tokens   INTEGER,
    peak_main_context_tokens INTEGER,
    live_context_tokens   INTEGER,        -- main peak since the last /compact (headline ctx%)
    context_window_tokens INTEGER,
    cost_usd              REAL,
    active_work_ms        INTEGER,
    cwd           TEXT,                -- starting cwd from the session.start span
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sessions_last_seen ON sessions(last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_title_nocase ON sessions(title COLLATE NOCASE);

-- Which registered repos a session touched (multi-repo join table).
-- is_primary=1 marks the repo the session started in. Populated at
-- ingest time + by `regin trace resolve-repos`. See lib/orm/models/trace.py.
CREATE TABLE IF NOT EXISTS session_repos (
    trace_id   TEXT NOT NULL,
    repo_id    INTEGER NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (trace_id, repo_id)
);
CREATE INDEX IF NOT EXISTS idx_session_repos_repo ON session_repos(repo_id);

-- Custom (user-authored) tags binding a session to one or more groups —
-- the M2M store behind the Sessions-list tag facet. Only custom tags live
-- here (source='manual'); the builtin category tags (user / topic-proposal /
-- system) are derived from sessions.origin at read time, never stored.
CREATE TABLE IF NOT EXISTS session_tags (
    trace_id   TEXT NOT NULL,
    tag        TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (trace_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_session_tags_tag ON session_tags(tag);

-- Per-assistant-turn token usage. One row per API response, keyed on
-- the transcript's message uuid so handler replays are idempotent.
-- Kept out of session_spans because turns aren't operations in the
-- timeline sense — they're metadata records.
CREATE TABLE IF NOT EXISTS turn_usage (
    trace_id               TEXT NOT NULL,
    turn_uuid              TEXT NOT NULL,
    turn_index             INTEGER NOT NULL,
    timestamp              TEXT NOT NULL,
    model                  TEXT,
    input_tokens           INTEGER NOT NULL DEFAULT 0,
    output_tokens          INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens  INTEGER NOT NULL DEFAULT 0,
    context_used_tokens    INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens       INTEGER,
    cost_usd               REAL,
    effort_level           TEXT,
    request_id             TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (trace_id, turn_uuid)
);
CREATE INDEX IF NOT EXISTS idx_turn_usage_trace_ts ON turn_usage(trace_id, timestamp);

-- Named ablation experiments that conceal H2 sections of a pattern's
-- SKILL.md when deploying it as a skill. Exactly one row per
-- (pattern_slug, name); at most one row per pattern_slug has active = 1.
-- The invariant is enforced in lib/experiments.activate().
CREATE TABLE IF NOT EXISTS experiments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_slug    TEXT NOT NULL,
    name            TEXT NOT NULL,
    conceal_spec    TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    activated_at    TEXT,
    UNIQUE(pattern_slug, name)
);

-- User accounts (local-only — not shared via git)
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    email         TEXT,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'editor',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_login    TEXT
);

-- Audit trail for web dashboard actions (local-only)
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users(id),
    username   TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Pattern deployments: tracks where each pattern has been deployed
-- as a Claude Code skill. scope='global' writes to ~/.claude/skills/,
-- scope='project' writes to <repo.path>/.claude/skills/ for the given project_id.
-- Local-only: preserved across rebuilds.
CREATE TABLE IF NOT EXISTS pattern_deployments (
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

-- Structural projection of session_spans, dual-written by the ingest
-- path so the frontend can load a session's full shape without dragging
-- along the potentially-large attributes JSON blob.
CREATE TABLE IF NOT EXISTS session_trace_map (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    span_id         TEXT NOT NULL,
    parent_id       TEXT,
    name            TEXT NOT NULL,
    kind            TEXT DEFAULT 'internal',
    start_time      TEXT NOT NULL,
    end_time        TEXT,
    duration_ms     INTEGER,
    status_code     TEXT DEFAULT 'UNSET',
    status_message  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (trace_id, span_id)
);
CREATE INDEX IF NOT EXISTS idx_session_trace_map_trace_id ON session_trace_map (trace_id);
CREATE INDEX IF NOT EXISTS idx_session_trace_map_parent_id ON session_trace_map (trace_id, parent_id);
CREATE INDEX IF NOT EXISTS ix_session_trace_map_parent_id ON session_trace_map (parent_id);

CREATE TABLE IF NOT EXISTS pattern_embeddings (
    pattern_id      INTEGER PRIMARY KEY NOT NULL,
    content_hash    TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    vector          BLOB NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS patterns_fts USING fts5(
    slug UNINDEXED,
    title, description, category, tag_names, body,
    tokenize='porter unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS pattern_docs_ad_patterns_fts
AFTER DELETE ON pattern_docs BEGIN
  DELETE FROM patterns_fts WHERE slug = OLD.slug;
END;

CREATE TABLE IF NOT EXISTS prompt_images (
    trace_id        TEXT NOT NULL,
    prompt_span_id  TEXT NOT NULL,
    idx             INTEGER NOT NULL,
    media_type      TEXT NOT NULL,
    bytes           BLOB NOT NULL,
    byte_size       INTEGER NOT NULL,
    sha256          TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (trace_id, prompt_span_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_prompt_images_trace ON prompt_images(trace_id);

-- User-managed prompt templates injectable into LLM/agent flows (topic
-- proposals today, more callers later). The built-in gitnexus-usage
-- row is seeded by the INSERT OR IGNORE just below the index.
CREATE TABLE IF NOT EXISTS prompt_templates (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                    TEXT NOT NULL UNIQUE,
    label                   TEXT NOT NULL,
    description             TEXT,
    body                    TEXT NOT NULL,
    kind                    TEXT NOT NULL DEFAULT 'fragment',
    variables               TEXT NOT NULL DEFAULT '[]',
    applies_to              TEXT NOT NULL DEFAULT '[]',
    default_for_providers   TEXT NOT NULL DEFAULT '[]',
    tags                    TEXT NOT NULL DEFAULT '[]',  -- JSON array of custom session-tag slugs a skeleton's runs self-apply (source='auto')
    agent                   TEXT,
    builtin                 INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_prompt_templates_slug ON prompt_templates(slug);
CREATE INDEX IF NOT EXISTS ix_prompt_templates_kind ON prompt_templates(kind);

-- Built-in prompt template, formerly seeded by alembic 0003. INSERT OR
-- IGNORE keyed on the UNIQUE slug keeps it idempotent across `regin init`
-- and `regin rebuild` (both run this file) and preserves a user-edited
-- body, since a matching slug is left untouched.
INSERT OR IGNORE INTO prompt_templates
    (slug, label, description, body, applies_to, default_for_providers, builtin)
VALUES (
    'gitnexus-usage',
    'Use GitNexus MCP for grounded topics',
    'Tell the proposal agent to call gitnexus MCP tools (query, context, route_map) and cite findings in each topic''s wiki narrative.',
    'Before drafting topics, use the gitnexus MCP tools to ground each
proposal in the repository''s actual execution flows — not just file
contents.

1. Confirm the repo is indexed: call `mcp__gitnexus__list_repos`. If
   the target repo is missing — or if any later gitnexus call warns
   that the index is stale — fall back to evidence-only drafting and
   note the gap in the wiki narrative (the `notes` field is not
   surfaced to reviewers).
2. For each candidate topic concept, run
   `mcp__gitnexus__query({query: "<concept>", goal: "scope topic boundary"})`.
   Group the resulting processes by `module` — a module plus its
   processes is a strong topic signal.
3. For the most central symbol of each candidate topic, call
   `mcp__gitnexus__context({name: "<symbol>"})`. A topic whose callers
   leak across many modules should be split or merged.
4. For UI/web topics, run `mcp__gitnexus__route_map` and reflect the
   API → handler → component edges in `refs` (roles: api, implementation).
5. In the wiki narrative for each topic, cite at least one gitnexus
   finding (process name, module name, or call edge) in addition to file paths.

If a gitnexus call fails, continue with evidence-only drafting and add
a note explaining which calls were skipped — do not invent edges.',
    '["external-agent"]',
    '["external-agent"]',
    1
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_branches_repo ON branches(repo_id);
CREATE INDEX IF NOT EXISTS idx_pattern_docs_category ON pattern_docs(category);
CREATE INDEX IF NOT EXISTS idx_doc_tags_doc ON doc_tags(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_tags_tag ON doc_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_rule_triggers_rule ON rule_triggers(rule_id);
CREATE INDEX IF NOT EXISTS idx_rule_triggers_checked ON rule_triggers(checked_at);
CREATE INDEX IF NOT EXISTS idx_rule_triggers_file ON rule_triggers(file_path);
CREATE INDEX IF NOT EXISTS idx_rule_triggers_session ON rule_triggers(session_id);
CREATE INDEX IF NOT EXISTS ix_rule_triggers_span_id ON rule_triggers(span_id);
CREATE INDEX IF NOT EXISTS ix_rule_triggers_suppressed ON rule_triggers(suppressed);
CREATE INDEX IF NOT EXISTS ix_rule_trigger_suppressions_trigger
    ON rule_trigger_suppressions(rule_trigger_id);
CREATE INDEX IF NOT EXISTS idx_rule_triggers_experiment ON rule_triggers(experiment_id);
CREATE INDEX IF NOT EXISTS idx_experiments_pattern ON experiments(pattern_slug);
CREATE INDEX IF NOT EXISTS idx_experiments_active ON experiments(pattern_slug, active);
CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(username);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_pattern_deployments_pattern ON pattern_deployments(pattern_slug);
CREATE INDEX IF NOT EXISTS idx_pattern_deployments_project ON pattern_deployments(project_id);
CREATE INDEX IF NOT EXISTS idx_pattern_deployments_scope ON pattern_deployments(scope);

-- Keyset-pagination support. The single-column order indexes above already
-- cover the common case, but the dashboards paginate with a (timestamp, id)
-- tiebreaker so two rows with identical timestamps don't collide on the
-- cursor boundary. These composites let SQLite seek straight to the cursor
-- position instead of filtering candidates by id after the range scan.
CREATE INDEX IF NOT EXISTS idx_rule_triggers_checked_id ON rule_triggers(checked_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_skill_reads_read_at_id ON skill_reads(read_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_id ON audit_log(created_at DESC, id DESC);

-- Topic-proposal ORM (see lib/orm/models/proposals.py).

CREATE TABLE IF NOT EXISTS proposal_runs (
    id                   TEXT PRIMARY KEY NOT NULL,
    repo_id              INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    provider             TEXT NOT NULL,
    scope                TEXT NOT NULL DEFAULT 'all',
    state                TEXT NOT NULL,
    agent_id             TEXT,
    complexity           TEXT NOT NULL DEFAULT 'standard',
    started_at           TEXT NOT NULL,
    completed_at         TEXT,
    updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
    error                TEXT,
    error_detail         TEXT,
    prompt_template_slugs TEXT NOT NULL DEFAULT '[]',
    evidence_hash        TEXT,
    regenerate_scope     TEXT,
    metadata_json        TEXT NOT NULL DEFAULT '{}',
    topic_request        TEXT
);

CREATE INDEX IF NOT EXISTS ix_proposal_runs_repo_id ON proposal_runs(repo_id);
CREATE INDEX IF NOT EXISTS ix_proposal_runs_state ON proposal_runs(state);

CREATE TABLE IF NOT EXISTS proposal_topics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL REFERENCES proposal_runs(id) ON DELETE CASCADE,
    topic_id            TEXT NOT NULL,
    label               TEXT NOT NULL,
    intent              TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active',
    aliases_json        TEXT NOT NULL DEFAULT '[]',
    refs_json           TEXT NOT NULL DEFAULT '[]',
    edges_json          TEXT NOT NULL DEFAULT '[]',
    commands_json       TEXT NOT NULL DEFAULT '[]',
    include_globs_json  TEXT NOT NULL DEFAULT '[]',
    exclude_globs_json  TEXT NOT NULL DEFAULT '[]',
    evidence_paths_json TEXT NOT NULL DEFAULT '[]',
    parent_id           TEXT,
    blurb               TEXT NOT NULL DEFAULT '',
    wiki_md             TEXT NOT NULL DEFAULT '',
    source              TEXT,
    review_status       TEXT,
    accepted_topic_id   TEXT,
    accepted_at         TEXT,
    merged_topic_id     TEXT,
    merged_at           TEXT,
    ignored_at          TEXT,
    downgraded_from     TEXT,
    downgraded_at       TEXT,
    replaced_existing   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_proposal_topics_run_id ON proposal_topics(run_id);
CREATE INDEX IF NOT EXISTS ix_proposal_topics_review_status ON proposal_topics(review_status);
CREATE UNIQUE INDEX IF NOT EXISTS ux_proposal_topics_run_topic ON proposal_topics(run_id, topic_id);

CREATE TABLE IF NOT EXISTS proposal_revisions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT NOT NULL REFERENCES proposal_runs(id) ON DELETE CASCADE,
    revision_number   INTEGER NOT NULL,
    parent_revision_id INTEGER,
    kind              TEXT NOT NULL DEFAULT 'generated',
    wiki_md           TEXT NOT NULL DEFAULT '',
    is_latest         INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    metadata_json     TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_proposal_revisions_run_id ON proposal_revisions(run_id);
CREATE INDEX IF NOT EXISTS ix_proposal_revisions_run_latest ON proposal_revisions(run_id, is_latest);
CREATE INDEX IF NOT EXISTS ix_proposal_revisions_is_latest ON proposal_revisions(is_latest);
CREATE UNIQUE INDEX IF NOT EXISTS ux_proposal_revisions_run_number ON proposal_revisions(run_id, revision_number);

CREATE TABLE IF NOT EXISTS proposal_revision_topics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id         INTEGER NOT NULL REFERENCES proposal_revisions(id) ON DELETE CASCADE,
    topic_id            TEXT NOT NULL,
    label               TEXT NOT NULL,
    intent              TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'active',
    aliases_json        TEXT NOT NULL DEFAULT '[]',
    refs_json           TEXT NOT NULL DEFAULT '[]',
    edges_json          TEXT NOT NULL DEFAULT '[]',
    commands_json       TEXT NOT NULL DEFAULT '[]',
    include_globs_json  TEXT NOT NULL DEFAULT '[]',
    exclude_globs_json  TEXT NOT NULL DEFAULT '[]',
    evidence_paths_json TEXT NOT NULL DEFAULT '[]',
    parent_id           TEXT,
    blurb               TEXT NOT NULL DEFAULT '',
    wiki_md             TEXT NOT NULL DEFAULT '',
    source              TEXT,
    review_status       TEXT,
    accepted_topic_id   TEXT,
    accepted_at         TEXT,
    merged_topic_id     TEXT,
    merged_at           TEXT,
    ignored_at          TEXT,
    downgraded_from     TEXT,
    downgraded_at       TEXT,
    replaced_existing   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_proposal_revision_topics_revision_id ON proposal_revision_topics(revision_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_proposal_revision_topics_revision_topic ON proposal_revision_topics(revision_id, topic_id);

CREATE TABLE IF NOT EXISTS proposal_feedback_threads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL REFERENCES proposal_runs(id) ON DELETE CASCADE,
    revision_id         INTEGER REFERENCES proposal_revisions(id) ON DELETE SET NULL,
    proposal_topic_id   TEXT,
    kind                TEXT NOT NULL DEFAULT 'comment',
    anchor_kind         TEXT NOT NULL DEFAULT 'general',
    anchor_json         TEXT NOT NULL DEFAULT '{}',
    quoted_text         TEXT,
    resolution_state    TEXT NOT NULL DEFAULT 'open',
    addressed_in_revision_id INTEGER REFERENCES proposal_revisions(id) ON DELETE SET NULL,
    created_by          TEXT NOT NULL DEFAULT 'user',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    metadata_json       TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_proposal_feedback_threads_run_id ON proposal_feedback_threads(run_id);
CREATE INDEX IF NOT EXISTS ix_proposal_feedback_threads_revision_id ON proposal_feedback_threads(revision_id);
CREATE INDEX IF NOT EXISTS ix_proposal_feedback_threads_topic_id ON proposal_feedback_threads(proposal_topic_id);
CREATE INDEX IF NOT EXISTS ix_proposal_feedback_threads_resolution ON proposal_feedback_threads(resolution_state);

CREATE TABLE IF NOT EXISTS proposal_feedback_comments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    feedback_thread_id INTEGER NOT NULL REFERENCES proposal_feedback_threads(id) ON DELETE CASCADE,
    author_kind        TEXT NOT NULL DEFAULT 'user',
    body               TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    metadata_json      TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS ix_proposal_feedback_comments_thread_id ON proposal_feedback_comments(feedback_thread_id);

CREATE TABLE IF NOT EXISTS graph_snapshots (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id                       INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    taken_at                      TEXT NOT NULL,
    reason                        TEXT NOT NULL,
    triggering_run_id             TEXT,
    triggering_proposal_topic_id  INTEGER,
    graph_json                    TEXT NOT NULL,
    wiki_pages_json               TEXT NOT NULL DEFAULT '{}',
    diff_summary_json             TEXT NOT NULL DEFAULT '{}',
    pinned                        INTEGER NOT NULL DEFAULT 0,
    is_latest                     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_graph_snapshots_repo_id ON graph_snapshots(repo_id);
CREATE INDEX IF NOT EXISTS ix_graph_snapshots_repo_taken ON graph_snapshots(repo_id, taken_at DESC);
CREATE INDEX IF NOT EXISTS ix_graph_snapshots_is_latest ON graph_snapshots(is_latest);
CREATE UNIQUE INDEX IF NOT EXISTS ux_graph_snapshots_repo_latest ON graph_snapshots(repo_id) WHERE is_latest = 1;

CREATE TABLE IF NOT EXISTS topic_audits (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id                       INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    kind                          TEXT NOT NULL,
    recorded_at                   TEXT NOT NULL DEFAULT (datetime('now')),
    severity                      TEXT NOT NULL,
    code                          TEXT NOT NULL,
    message                       TEXT NOT NULL,
    topic_ids_json                TEXT NOT NULL DEFAULT '[]',
    paths_json                    TEXT NOT NULL DEFAULT '[]',
    aliases_json                  TEXT NOT NULL DEFAULT '[]',
    triggering_run_id             TEXT,
    triggering_proposal_topic_id  INTEGER,
    snapshot_id                   INTEGER,
    fix_action                    TEXT
);

CREATE INDEX IF NOT EXISTS ix_topic_audits_repo_id ON topic_audits(repo_id);
CREATE INDEX IF NOT EXISTS ix_topic_audits_repo_kind_code ON topic_audits(repo_id, kind, code);
CREATE INDEX IF NOT EXISTS ix_topic_audits_triggering_run ON topic_audits(triggering_run_id);

-- Per-topic-ref content fingerprints captured at wiki-write time, the
-- substrate for code-driven topic drift detection (lib/topics/ref_digest.py).
-- One row per (repo_id, topic_id, path); re-capture is an idempotent upsert.
CREATE TABLE IF NOT EXISTS topic_ref_digests (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id             INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    topic_id            TEXT NOT NULL,
    path                TEXT NOT NULL,
    role                TEXT,
    content_hash        TEXT NOT NULL,
    embedding_json      TEXT,
    embedding_model_id  TEXT,
    captured_at         TEXT NOT NULL,
    -- JSON list of wiki-cited identifier tokens present in the ref at capture
    -- (lib/topics/wiki_anchors.py); NULL = pre-anchor row or topic had no wiki.
    anchors_json        TEXT,
    -- Repo HEAD when the digest was captured — the git base the drift judge
    -- diffs against; NULL = pre-stamp row (judge gets no diff evidence).
    captured_commit     TEXT,
    UNIQUE (repo_id, topic_id, path)
);
CREATE INDEX IF NOT EXISTS ix_topic_ref_digests_repo_id ON topic_ref_digests(repo_id);
CREATE INDEX IF NOT EXISTS ix_topic_ref_digests_topic_id ON topic_ref_digests(topic_id);

CREATE TABLE IF NOT EXISTS payload_schema_drift (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent               TEXT NOT NULL DEFAULT 'claude',
    -- 'tool' (the subject is a tool name) | 'hook_event' (the subject is a
    -- hook event name like 'PreToolUse'). Orthogonal to `agent`; the
    -- subject identity itself lives in `tool_name`.
    subject_kind        TEXT NOT NULL DEFAULT 'tool',
    tool_name           TEXT NOT NULL,
    drift_kind          TEXT NOT NULL,
    field_path          TEXT NOT NULL,
    expected            TEXT,
    sample_value        TEXT NOT NULL,
    sample_payload_sha  TEXT,
    claude_version      TEXT,
    first_seen          TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen           TEXT NOT NULL DEFAULT (datetime('now')),
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'pending',
    CONSTRAINT uq_payload_schema_drift_key
        UNIQUE (agent, subject_kind, tool_name, drift_kind, field_path, claude_version)
);

CREATE INDEX IF NOT EXISTS ix_payload_schema_drift_tool ON payload_schema_drift(tool_name);
CREATE INDEX IF NOT EXISTS ix_payload_schema_drift_status ON payload_schema_drift(status);
CREATE INDEX IF NOT EXISTS ix_payload_schema_drift_agent ON payload_schema_drift(agent);
CREATE INDEX IF NOT EXISTS ix_payload_schema_drift_kind ON payload_schema_drift(subject_kind);

-- Tag seeds are user-curated via $TAGS_CONFIG_PATH
-- (default `~/.local/share/regin/config/tags.yaml`) and applied by
-- `lib/db_rebuild.py`. No tags are baked into the schema.
