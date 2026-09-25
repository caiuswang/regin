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
--
-- Two extra axes keep the audit trail and the /live "queued" chip feed
-- separate. `kind` says what the row IS (steer | answer | decision): only
-- steers are ever chip-eligible; an AskUserQuestion answer or a permission
-- decision is audit-only. `state` is the chip lifecycle, advanced one-way by
-- observed events, never by a clock: pending → consumed (the transcript
-- queued/processed the body) | closed (session ended, or born closed because
-- another store is authoritative for display) | dismissed (operator removed
-- the chip). `state_at` stamps the transition.
CREATE TABLE IF NOT EXISTS bridge_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    sender          TEXT,
    delivered       INTEGER NOT NULL DEFAULT 0,
    delivery_detail TEXT,
    delivery_path   TEXT,
    is_test         INTEGER NOT NULL DEFAULT 0,
    kind            TEXT NOT NULL DEFAULT 'steer',
    state           TEXT NOT NULL DEFAULT 'pending',
    state_at        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    delivered_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_bridge_messages_trace ON bridge_messages(trace_id);
CREATE INDEX IF NOT EXISTS idx_bridge_messages_created ON bridge_messages(created_at DESC);

-- Images staged by a web composer (the /live card, the session-detail
-- follow-up) on their way into a live session. Distinct from `prompt_images`,
-- which is the settled record keyed to a real prompt span: a row here has no
-- span yet, because the prompt it belongs to has not been typed. `idx` is the
-- `[Image #N]` number handed to the composer, allocated per trace so it shares
-- the terminal's own numbering; the marker travels in the delivered text and
-- is what binds the bytes to the span once it lands (`claimed_span_id`).
-- `dispatched_sha` is the disambiguator: the terminal's own paste numbers
-- `[Image #N]` from 1 in the SAME trace, so a marker alone cannot say whether
-- a prompt carried THIS image. It holds the digest of the exact text regin
-- delivered, and a row binds only to a span whose text matches it.
CREATE TABLE IF NOT EXISTS bridge_attachments (
    id              TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    idx             INTEGER NOT NULL,
    media_type      TEXT NOT NULL,
    bytes           BLOB NOT NULL,
    byte_size       INTEGER NOT NULL,
    sha256          TEXT NOT NULL,
    file_path       TEXT,
    dispatched_sha  TEXT,
    claimed_span_id TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_bridge_attachments_trace ON bridge_attachments(trace_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bridge_attachments_trace_idx
    ON bridge_attachments(trace_id, idx);

-- Sessions regin launched itself through the Claude Agent SDK
-- (`lib/agent_sdk/`). This is to the SDK tier what `bridge_panes` is to the
-- tmux tier: a canonical, mutable, one-row-per-session identity record, and
-- the fact steering routes on — an SDK-owned session answers an
-- AskUserQuestion over a typed in-process channel, a user-started one can only
-- be reached by typing into its pane. `pid` is the `claude` child; `status`
-- ('starting' | 'running' | 'exited' | 'failed') keeps a crashed runner's row
-- distinguishable from a live one after a server restart.
-- `cli_session_id` is the child `claude` session this run is ALSO traced as.
-- The child loads the user's hooks, so it writes a second trace under its own
-- id; the serve-time reader unions the pair into one session. NULL until the
-- child reports its id (first message of the first turn), and forever for a
-- run whose child never spoke — readers treat NULL as "not aliased".
CREATE TABLE IF NOT EXISTS agent_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id       TEXT NOT NULL UNIQUE,
    status         TEXT NOT NULL DEFAULT 'starting',
    pid            INTEGER,
    cwd            TEXT,
    model          TEXT,
    detail         TEXT,
    cli_session_id TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_runs_cli_session_id
    ON agent_runs(cli_session_id);

-- Every `claude` session a run has been traced as. `agent_runs.cli_session_id`
-- holds one child and stays pinned to the FIRST — which is what the trace view
-- unions on, because a `/clear` opens a new conversation rather than
-- continuing the old one. A run still accumulates children (the same runner
-- drives each one in turn), and this is where the whole alias group lives, so
-- `find_run`, the dual-writer guard, the resume picker and the settle sweep
-- can see an heir instead of only the first child.
-- One row per (run, child) pair; neither side is unique alone. A run has many
-- children (that is what `/clear` makes), and a child appears under several
-- runs, because resuming keeps the child's session id and leaves the previous
-- `exited` row still naming it — the shape `reap_orphaned_runs` depends on.
CREATE TABLE IF NOT EXISTS agent_run_sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id       TEXT NOT NULL,
    cli_session_id TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (trace_id, cli_session_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_run_sessions_trace_id
    ON agent_run_sessions(trace_id);

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
-- Partial: the blocker feed asks which sessions hold an unresolved decision
-- span on every poll. The predicate mirrors blockers._decision_predicate() —
-- SQLite only uses a partial index when the query's terms are syntactically
-- implied by the index's, so the two must not drift.
CREATE INDEX IF NOT EXISTS idx_session_spans_pending ON session_spans(trace_id) WHERE status_code = 'PENDING' AND (name IN ('permission.request','tool.AskUserQuestion','tool.ExitPlanMode') OR span_id LIKE 'permreq-%');
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

-- Machine credentials for the non-JWT API surface. `key_hash` (SHA-256 of the
-- full `rgn_<32 hex>` key) is the ONLY lookup path: the plaintext is shown once
-- at creation and never stored, so a lost key is re-minted, not recovered.
-- `key_prefix` names a row in the UI without carrying the secret. A key is a
-- different principal from a user, not a weaker one — resolving one never
-- produces a user session (lib/auth.py require_scope).
CREATE TABLE IF NOT EXISTS api_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    key_prefix   TEXT NOT NULL,
    scopes       TEXT NOT NULL DEFAULT '[]',
    created_by   INTEGER,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    revoked_at   TEXT
);

-- A feature: its own row and its own primary key. The population IS this
-- table. It used to be a five-arm union of strings over session_features,
-- defects, agent_task_links and agent_tasks, so four surfaces answered "what
-- counts as a feature" four different ways — and none of the arms was the
-- done-claim, which is how a feature could be shipped without being listed.
--
-- external_ref carries NO referential integrity requirement: a remote issue
-- somebody deleted leaves this row perfectly valid. It is a record of what was
-- built here, not a mirror of somebody else's database. It also absorbs the
-- whole of the old issue_cache table — title, url, sync_error and synced_at
-- each get a column, and the state history lives in feature_events.
CREATE TABLE IF NOT EXISTS features (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    body         TEXT,
    origin       TEXT NOT NULL DEFAULT 'native',
    external_ref TEXT,
    external_url TEXT,
    sync_error   TEXT,
    synced_at    TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    -- The registered repo this feature belongs to (repos.id). Declared, never
    -- inferred: NULL is the first-class "in no project" scope, not a gap
    -- waiting to be filled from a cwd, a branch name or a task's project.
    -- Last because `0029` appends it: a fresh install and a migrated one must
    -- agree column-for-column, order included.
    project_id   INTEGER,
    CONSTRAINT uq_features_origin_ref UNIQUE (origin, external_ref)
);
CREATE INDEX IF NOT EXISTS idx_features_project ON features(project_id);

-- One declared step of a feature's life. APPEND-ONLY: never UPDATEd, never
-- DELETEd. stage, rounds, claimed_at and the escape verdict are all queries
-- over this table rather than stored columns, so a counter and the fact it
-- counts cannot drift; undoing a ship is a `restarted` row, not a column reset.
--
-- The total order is `id`, NOT `at`. Two events written in one transaction
-- share a timestamp, so a clock-ordered read would make a ship-then-restart
-- depend on timestamp precision. `at` is for display.
--
-- declared_by is NOT NULL, so there is no unowned state change: `operator`,
-- `run:<id>` or `sync:<provider>`.
CREATE TABLE IF NOT EXISTS feature_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id  INTEGER NOT NULL REFERENCES features(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    declared_by TEXT NOT NULL,
    trace_id    TEXT,
    at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_feature_events_feature
    ON feature_events(feature_id, id);

-- Images attached to a feature. The mirror of `agent_task_images`, one level
-- up: a feature's mockup is read THROUGH a task's `feature_id` at launch
-- rather than copied onto the slices when the spec is transcribed, so
-- replacing it corrects the next attempt of every slice already cut.
-- `idx` is a per-feature order and a freed one is never reissued, which is
-- what lets the byte-serving GET be cached hard.
CREATE TABLE IF NOT EXISTS feature_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id INTEGER NOT NULL,
    idx        INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    bytes      BLOB NOT NULL,
    byte_size  INTEGER NOT NULL,
    sha256     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CONSTRAINT uq_feature_images_idx UNIQUE (feature_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_feature_images_feature
    ON feature_images(feature_id);


-- Captured task ideas — the intent layer in front of the SDK launcher. No
-- execution parameter lives here by construction (no cwd, no model, no
-- permission mode): capture is reachable with a third-party key, so the worst
-- a leaked one can do is add a backlog row.
-- UNIQUE(source, external_id) is the idempotency guarantee for redelivered
-- third-party captures; NULL external ids do not collide in a unique index, so
-- hand-typed captures coexist freely.
CREATE TABLE IF NOT EXISTS agent_tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    body        TEXT,
    status      TEXT NOT NULL DEFAULT 'idea',
    source      TEXT NOT NULL,
    external_id TEXT,
    tags_json   TEXT,
    links_json  TEXT,
    created_by  INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    -- The registered repo this task belongs to (repos.id) — what `project_id`
    -- already means in pattern_deployments. Nullable and with no FK, exactly
    -- like that column: a repo dropped from the registry leaves its tasks in
    -- the backlog rather than cascading them away.
    project_id  INTEGER,
    -- Ordered enum stored as text, paired with its integer sort key. Two
    -- columns for one axis on purpose: ordering by a CASE over the words
    -- cannot use an index, and its value cannot be read back off a serialized
    -- row, so the keyset cursor for page two cannot be built.
    -- lib.orm.models.tasks.PRIORITY_ORDER is the mapping.
    priority      TEXT NOT NULL DEFAULT 'none',
    priority_rank INTEGER NOT NULL DEFAULT 4,
    -- The one feature this task is a step of, or NULL. ONE column, which is
    -- what makes "a task holding two feature keys" unwritable — the shape
    -- three refusals in lib/tasks/links.py and an `ambiguous` CTE in every
    -- reader used to defend against.
    feature_id    INTEGER REFERENCES features(id) ON DELETE SET NULL,
    -- users.id, claimed by an authenticated human about themselves. No FK: the
    -- serializer resolves an unknown id to null rather than cascading.
    assignee      INTEGER,
    -- What the step is (spec | build | fix | task), orthogonal to status.
    -- Declared at create, never inferred from the title.
    kind          TEXT NOT NULL DEFAULT 'task',
    CONSTRAINT uq_agent_tasks_source_external UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_feature ON agent_tasks(feature_id);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_project ON agent_tasks(project_id);
-- The task feed keyset-paginates on (created_at DESC, id DESC), filtered by
-- status. Without these two the page is a full scan plus a sort at any size.
CREATE INDEX IF NOT EXISTS idx_agent_tasks_created ON agent_tasks(created_at, id);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_status_created ON agent_tasks(status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_priority ON agent_tasks(priority_rank, created_at, id);


-- One attempt at a task, append-only. `trace_id` joins agent_runs.trace_id,
-- which brings cost, spans and grade with it; it is NULL for the window
-- between writing this row and the launch that mints the id, so a crash there
-- leaves a visible `starting` row instead of an untracked child.
-- `status` is a claim about intent, not proof of liveness (see agent_runs).
-- UNIQUE(task_id, attempt) is the arbiter for two concurrent starts: both
-- compute the same next attempt, one wins the insert, the loser is refused —
-- the same identity-based resolution as UNIQUE(source, external_id) above.
CREATE TABLE IF NOT EXISTS agent_task_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          INTEGER NOT NULL,
    trace_id         TEXT,
    attempt          INTEGER NOT NULL DEFAULT 1,
    status           TEXT NOT NULL DEFAULT 'starting',
    started_at       TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at         TEXT,
    exit_reason      TEXT,
    verify_exit_code INTEGER,
    -- The parameters THIS attempt spends, written by the arm route alone
    -- (user-authenticated). They sat on the task, so trying the same work with
    -- different ones meant creating a second task — which is how "a sub-task
    -- is a rerun" became a shape the model had to carry. An `armed` run row is
    -- the pending attempt: parameterised, not yet launched.
    params_json      TEXT,
    verify_cmd       TEXT,
    autorun          INTEGER NOT NULL DEFAULT 0,
    -- Who settled this attempt: 'agent' (its own `regin task finish`) or
    -- 'session' (the SDK callback, or a launch that never started). The
    -- callback only knows the session ended, which is an inference and a wrong
    -- one for a session aborted and later resumed by hand; the agent's report
    -- is the fact the chain waits on. Without this column `ended_at` made the
    -- two indistinguishable and whichever arrived first won. NULL = unsettled,
    -- or a row settled before anyone recorded it.
    settled_by       TEXT,
    CONSTRAINT uq_agent_task_runs_attempt UNIQUE (task_id, attempt)
);

CREATE INDEX IF NOT EXISTS idx_agent_task_runs_task ON agent_task_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_agent_task_runs_trace ON agent_task_runs(trace_id);

-- The task DAG: `task_id` waits for `depends_on_id`. An edge table rather than
-- an `after: []` column because both directions are queried, and acyclicity is
-- enforced by the writer before the insert.
CREATE TABLE IF NOT EXISTS agent_task_deps (
    task_id       INTEGER NOT NULL,
    depends_on_id INTEGER NOT NULL,
    -- 'requires' (the dependent consumes this result, so a failure parks it)
    -- or 'after' (ordering only, satisfied by a failure as much as a success).
    kind          TEXT NOT NULL DEFAULT 'requires',
    PRIMARY KEY (task_id, depends_on_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_task_deps_depends_on
    ON agent_task_deps(depends_on_id);

-- Images attached to a captured idea. Keyed to the TASK, not to a trace, which
-- is what separates this from `bridge_attachments`: a task has no trace until
-- someone runs it — maybe never, maybe three times — so the bytes are re-read
-- on every attempt instead of being claimed and spent by one prompt. `idx` is
-- the per-task display and delivery order; no `[Image #N]` marker travels in
-- the text, because a task's prompt is its title and body and the images ride
-- the launch as content blocks.
CREATE TABLE IF NOT EXISTS agent_task_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    idx        INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    bytes      BLOB NOT NULL,
    byte_size  INTEGER NOT NULL,
    sha256     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CONSTRAINT uq_agent_task_images_idx UNIQUE (task_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_agent_task_images_task
    ON agent_task_images(task_id);

-- One defect: something a feature got wrong, addressed by the feature's
-- integer id. Its own table rather than a memory because a defect carries
-- lifecycle axes a memory has none of — open/closed, a round, attributed or
-- not — and folding those into tags would put two axes on one column.
--
-- `feature_id` NULL is *unattributed*, a first-class state: a wrong parent
-- inflates one feature's round count and exonerates another, so a defect waits
-- unattributed until blame names its origin. NULLs do not collide in a SQLite
-- unique index, so unattributed reports never dedup against each other — only
-- an attributed (feature, symptom) pair is idempotent.
CREATE TABLE IF NOT EXISTS defects (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id         INTEGER REFERENCES features(id) ON DELETE SET NULL,
    symptom            TEXT NOT NULL,
    symptom_key        TEXT NOT NULL,
    -- Who REPORTED it. Never who caused it; the name carries the invariant,
    -- and neither direction is derived from the other.
    reported_by_trace  TEXT,
    suspect_paths_json TEXT,
    round              INTEGER NOT NULL DEFAULT 1,
    status             TEXT NOT NULL DEFAULT 'open',
    missing_check      TEXT,
    opened_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at          TEXT,
    -- The task that will close this defect. At most one per defect; one task
    -- may serve many. regin never writes either side from the other.
    fix_task_id        INTEGER,
    -- The feature's last event at the instant blame was assigned. An escape is
    -- `blamed_event.kind = 'shipped'` — a pointer at an immutable row, so it
    -- cannot go stale and has no "we never looked" reading to count as a gap.
    blamed_event_id    INTEGER REFERENCES feature_events(id) ON DELETE SET NULL,
    -- The triage queue's verdict, orthogonal to `status`: that column asks
    -- whether the bug is still real, this one whether anyone has routed it.
    -- NULL = still in the queue. 'declined' and 'snoozed' are the two ways
    -- out that are not an attribution or a merge.
    triage_state       TEXT,
    triage_reason      TEXT,
    -- `activity_seq` at the instant of a snooze. A snooze ends when the two
    -- differ, which is an event and not a deadline — no clock is read.
    snooze_watermark   INTEGER,
    -- Writes to this row, counted. `updated_at` cannot serve as the activity
    -- marker: it has second resolution, so two writes inside one second are
    -- indistinguishable and "did anything happen" would answer no.
    activity_seq       INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_defects_feature_symptom UNIQUE (feature_id, symptom_key)
);

CREATE INDEX IF NOT EXISTS idx_defects_feature ON defects(feature_id);
-- An unattributed defect dedups within its session: `regin defects open` is
-- run by an agent, and a retried command after a transient failure would
-- otherwise become a second row nobody notices.
CREATE UNIQUE INDEX IF NOT EXISTS uq_defects_session_symptom
    ON defects(reported_by_trace, symptom_key) WHERE feature_id IS NULL;


-- The screenshot a defect was reported with. The third `ImageSet` owner and
-- the only one whose bytes are evidence rather than model input: a task's and
-- a feature's images ride an agent's prompt, these are what the reporter saw.
-- ON DELETE CASCADE because a defect's evidence has no meaning without the
-- report, unlike a feature's mockup, which outlives any one slice.
CREATE TABLE IF NOT EXISTS defect_images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    defect_id  INTEGER NOT NULL REFERENCES defects(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    bytes      BLOB NOT NULL,
    byte_size  INTEGER NOT NULL,
    sha256     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CONSTRAINT uq_defect_images_idx UNIQUE (defect_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_defect_images_defect
    ON defect_images(defect_id);
CREATE INDEX IF NOT EXISTS idx_defects_fix_item ON defects(fix_task_id);
CREATE INDEX IF NOT EXISTS idx_defects_triage ON defects(triage_state);

-- Which feature a session was building. Mirrors session_repos; an
-- unattributed session is the ABSENCE of a row, never a sentinel.
CREATE TABLE IF NOT EXISTS session_features (
    trace_id    TEXT NOT NULL,
    feature_id  INTEGER NOT NULL REFERENCES features(id) ON DELETE CASCADE,
    is_primary  INTEGER NOT NULL DEFAULT 0,
    provenance  TEXT NOT NULL DEFAULT 'declared',
    round       INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (trace_id, feature_id)
);
CREATE INDEX IF NOT EXISTS idx_session_features_feature
    ON session_features(feature_id);

-- Tag seeds are user-curated via $TAGS_CONFIG_PATH
-- (default `~/.local/share/regin/config/tags.yaml`) and applied by
-- `lib/db_rebuild.py`. No tags are baked into the schema.


-- A conversation about something, with a polymorphic subject. Generic from the
-- first row: `proposal_feedback_threads` is the same shape welded to one
-- subject, and widening it would have made every reader of that table carry a
-- kind column it has no second value for. This one is widened exactly as far
-- as tasks need — `subject_kind` holds 'task' and nothing else today.
--
-- `subject_id` is TEXT because a subject id is opaque here, and no FK points
-- at the subject: a task's own delete path removes its threads, and the
-- cascade below removes their comments.
--
-- `resolution_state` means only *has this conversation been dealt with*.
-- Whether the ticket is closed is the task's own `status`, and folding the two
-- would put two axes on one column.
CREATE TABLE IF NOT EXISTS comment_threads (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_kind     TEXT NOT NULL,
    subject_id       TEXT NOT NULL,
    resolution_state TEXT NOT NULL DEFAULT 'open',
    created_by       INTEGER,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_comment_threads_subject ON comment_threads(subject_kind, subject_id);

-- One comment in a flat thread. `author_kind` and `author_id` are two axes,
-- not one: an agent comment carries a kind and a NULL id, which a single
-- nullable author column could not distinguish from a user regin lost track of.
CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   INTEGER NOT NULL REFERENCES comment_threads(id) ON DELETE CASCADE,
    author_kind TEXT NOT NULL DEFAULT 'user',
    author_id   INTEGER,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_comments_thread ON comments(thread_id);
