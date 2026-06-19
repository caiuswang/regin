# Architecture

System internals for developers working on regin itself. For setup and usage, see [README.md](README.md).

## How It Works

```
Pattern guides (SKILL.md)  +  Rule engines (.grit, …)
            |                          |
            v                          v
   SQLite tracking +  Flask API  +  hook_manager
                       |
                       v
              Vue 3 SPA (frontend/)
                       |
                       v
   Active provider skills dir (Claude default: ~/.claude/skills/)
```

regin is a harness for AI coding agents. It manages three asset classes —
**patterns** (markdown procedure guides), **rule engines** (Grit and friends),
and **trace** (session + span data) — and deploys them into the active
provider's skills directory so the agent reads them at the right moment.

- **Patterns** are user-authored. Create one via the web UI's *New pattern*
  flow, or import a versioned skill bundle from regin-skillhub. They live as
  `SKILL.md` files under `$PATTERNS_DIR`, get indexed in SQLite, and get
  deployed via `lib/skills/skill_deployer.py`.
- **Rule engines** (`lib/rule_engines/`) plug structural lints / rewriters
  into `PostToolUse` hooks. The Grit adapter is built in; new engines slot
  next to it.
- **Trace** ingests hook events and renders sessions, turns, token rollups,
  and skill-read correlation.

## Module Layout

### Framework layer

| Module | Purpose |
|--------|---------|
| `lib/settings.py` | Typed `Settings(BaseSettings)` via pydantic-settings — env > `settings.local.json` > `settings.json` > defaults; `reload_settings()` refreshes the singleton in place. Also hosts the settings.json CRUD (+ config-file path constants) relocated from `lib/config.py` |
| `lib/orm/engine.py` | SQLAlchemy `Engine` + `SessionLocal` / `AuthSessionLocal` factories; also the raw-sqlite helpers (`get_connection`, `init_db`, `db_exists`, `load_schema_sql`) + canonical `DB_PATH` for the paginated trace reads |
| `lib/orm/base.py` | Shared SQLModel base (`Base` + `metadata`) — the single MetaData Alembic targets |
| `lib/orm/models/` | Typed tables grouped by domain: `users.py` (accounts, audit log), `sync.py` (repos, branches), `patterns.py` (pattern docs, tags, deployments), `rules.py` (rule triggers, experiments), `trace.py` (session spans/sessions, turn usage, skill reads, plan sessions, prompt images), `proposals.py` (topic proposal runs/revisions, graph snapshots, feedback), `prompts.py`, `payload_schema_drift.py` |
| `lib/logging_setup.py` | `configure_logging()` + `get_logger()` using structlog |
| `lib/providers/` | Provider adapters (`claude`, `codex`, `generic`) for skills/hooks/session path conventions and capability gating |
| `alembic/` | Schema migrations; env wires DB URL + MetaData from `lib.orm` |

### Hooks

Two modules handle hook ingestion/tracing. They are complementary:

| Module | Responsibility |
|--------|----------------|
| `hook_manager/` | **Dispatcher.** Single entry `python -m hook_manager <EventName>` reads stdin, runs every registered handler for that event, merges their `HookResponse`s, writes the final JSON to stdout. Used by the new `settings.json` `command` field. The team registry (`registry.py`, or `describe_handlers()` at runtime) is the live list of which events have wired handlers; provider-kind hooks like the worktree/elicitation events are intentionally left unwired (see `hook_manager/README.md`). Owns `core.py` (Handler, matchers), `registry.py` (team-wide handlers), `custom_registry.py` (per-user opt-in), `runner.py` (stdin→stdout pipeline), `merge.py` (precedence rules). |
| `lib/hook_plugin.py` | **Tracing/ingest helper.** A library used by standalone hook scripts under `scripts/` that want to POST spans to the regin web server (`/api/session-spans`, `/api/skill-reads`, `/api/rule-triggers`). Provides `HookContext`, retry loop with jitter, error-log rotation. Not invoked by `hook_manager` — scripts call it directly when they need to emit trace events. |

### Domain layer

| Module | Purpose |
|--------|---------|
| `lib/patterns/pattern_importer.py` | Import a versioned skill bundle (from regin-skillhub) as a local pattern |
| `lib/patterns/pattern_promoter.py` | Promote a local pattern to a versioned skill bundle (publishes to regin-skillhub) |
| `lib/patterns/pattern_router.py` | Pattern-to-procedure matching helpers used by rule engines and the web UI |
| `lib/tags/tag_manager.py` | Auto-tagging from layers, annotations, repo names |
| `lib/sync/git_ops.py` | Git subprocess wrapper used by `repo_discovery` |
| `lib/sync/repo_discovery.py` | Manage `settings.repo_paths`: registration, default-branch detection |
| `lib/db_rebuild.py` | Rebuild DB from git-tracked files (patterns, tags, rules) |
| `lib/auth.py` | JWT authentication, password hashing, role decorators (uses SQLModel) |
| `lib/audit.py` | Audit trail for web dashboard actions (uses SQLModel) |
| `lib/skills/skill_sync.py` | Push/pull skills via the active provider global skills dir (Claude default: `~/.claude/skills/`) |
| `lib/skills/skill_deployer.py` | Deploy patterns as provider skills (Claude fully supported; other providers are capability-gated stubs) |
| `lib/languages/` | Source-language registry (`Language` dataclass + per-language files under `java.py`). Each entry declares `id`, `file_extensions`, a `parse_class_metadata` parser, and an open-ended `framework_hooks` mapping. New languages plug in here. |
| `lib/rule_engines/` | `RuleEngine` Protocol + built-in adapters (`grit.py`, `bundle.py`, `radon_engine.py`). Declared in `settings.rule_engines`. |
| `lib/rules/grit_rule_index.py` | Grit-specific facade: parses `.grit` files via the engine and generates `rules.json` and `RULES.md`. |
| `lib/tokens/` | `model_windows.py` (model → context-window lookup; `window_for` / `infer_window` resolve variants like `claude-opus-4-7[1m]`), `token_estimator.py` (tiktoken-based text + image token estimates used by trace ingest), `pricing.py` (per-model cost table). |
| `lib/trace/transcript_usage.py` | Parses transcript JSONL (Claude default: `~/.claude/projects/*/<session>.jsonl`) into `TurnUsage` / `TranscriptUsage`. Used by the turn-trace hook and `regin trace backfill-tokens`. |
| `lib/trace/trace_service/` | Package — `ingest.py` writes session + span rows; `queries.py` powers `/api/sessions` and `/api/session-spans`. Import path stays `from lib.trace.trace_service import …`. |
| `web/app.py` | Flask JSON API backend; registers all `web/blueprints/*` |
| `frontend/src/` | Vue 3 SPA (views, components, composables, router) |
| `scripts/setup.sh` | New machine setup script |

## Database

Two databases serve different purposes, controlled by the `mode` setting:

### Auth/audit database

- **`standalone` mode** (default): Uses local SQLite (`db/regin.db`). No remote database needed.
- **`shared` mode**: Uses MySQL. All team members connect to the same instance.

| Table | Purpose |
|-------|---------|
| `users` | Accounts, password hashes, roles (admin/editor/viewer) |
| `audit_log` | Who did what in the web dashboard |

In `shared` mode, configure MySQL via `database_url` in `config/settings.local.json` or `REGIN_DATABASE_URL` env var.
Initialize tables: `regin users init-db`

### SQLite (local — per-machine cache)

At `db/regin.db`. Rebuilt from on-disk files via `regin rebuild`.

| Table | Source / Purpose |
|-------|-----------------|
| `repos`, `branches` | Repos explicitly registered via `regin add-repo` or the `/repos` web UI; one `branches` row per tracked branch |
| `pattern_docs` | Parsed from `$PATTERNS_DIR/*/SKILL.md` (default `~/.local/share/regin/patterns/`) |
| `tags`, `doc_tags` | Seeded from `$TAGS_CONFIG_PATH` (default `~/.local/share/regin/config/tags.yaml`) |
| `experiments` | Ablation experiments (local) |
| `rule_triggers` | Rule-engine check log (local) |

### Trace tables (session analytics)

Populated live by the `hook_manager` handlers and surfaced under the **Trace** menu in the UI.

| Table | Purpose |
|-------|---------|
| `session_spans` | OpenTelemetry-style span log (`session.start`, `turn`, `prompt`, `tool.*`, plan-mode spans, etc.) |
| `turn_usage` | Per-API-call token usage, keyed `(trace_id, turn_uuid)`; see [`docs/trace/TURN_USAGE.md`](docs/trace/TURN_USAGE.md) |
| `sessions` | Per-session aggregates: title, counters, model, token totals (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `peak_context_tokens`, `context_window_tokens`) rolled up from `turn_usage` |
| `skill_reads` | Skill-invocation events (`source` = `launch` / `invoke` / `read`); see *Skill Invocation Tracing* below |
| `plan_sessions` | Durable session→plan mapping; populated on attributable plan touches (see *Plan Mode Session Tracing* below) |
| `agent_messages` | Canonical store for `send_to_user` agent→human messages (typed, supersedable, read/ack state); see *Agent Messages (send_to_user inbox)* below |

Token counters on `sessions` are part of the baseline schema (`db/schema.sql`, anchored by `alembic/versions/0001_baseline.py`).

### Agent memory database

A third database, at `db/regin_memory.db` (override via `settings.agent_memory.db_path`). It is **self-initializing** — the memory engine creates its own schema on first use, so its tables are deliberately absent from `db/schema.sql` and Alembic, and accumulated experience survives `regin init` / `rebuild`. See *Agent Memory (cross-session experience)* below.

## Authentication

- **User storage**: lives in the auth/audit database selected by `mode` — local SQLite in `standalone`, shared MySQL in `shared`. See the **Database** section above.
- **JWT tokens** (HS256, 1-week expiry) signed with `config/jwt_secret.txt` (gitignored, auto-generated per machine)
- **Password hashing**: PBKDF2-HMAC-SHA256 with random salt
- **Three roles**: admin (full access), editor (mutate patterns/skills/rules/settings), viewer (read-only)
- **Decorators**: `@require_auth` (any logged-in user), `@require_editor` (editor or admin), `@require_role('admin')` (admin only)
- GET endpoints are open (no auth required for reading)
- POST endpoints require `@require_editor` or `@require_role('admin')`

## Config System

Two-layer merge: `config/settings.json` (shared, git-tracked) + `config/settings.local.json` (local, gitignored). Local overrides shared.

| Setting | Scope | Default |
|---------|-------|---------|
| `repo_paths` | local | `[]` (managed by `/repos` page and `regin add-repo` / `regin remove-repo`; each entry is an absolute git working tree path) |
| `active_provider` | local | `claude` |
| `providers` | local | `{}` (optional per-provider path overrides) |
| `experimental_providers` | local | `false` — when true, surfaces `codex` and `generic` in the settings UI and `/api/providers` |
| `skills_dir` | local | `~/.claude/skills` (legacy Claude fallback) |
| `web_port` | shared | 8321 |
| `mode` | local | `standalone` (also: `shared`) |
| `database_url` | local | `None` (required when `mode: shared`) |
| `skillhub_url` | local | `http://127.0.0.1:8322` (regin-skillhub server) |
| `rule_engines` | shared | `[]` — populate to enable lint chrome (see below) |
| `language_extensions` | shared | `{}` — map a language id → file extensions so an engine can target a language `lib/languages/` doesn't ship (see Rule engines → Language routing) |
| `patterns_dir`, `grit_dir`, `tags_path`, `auto_tag_rules_path` | local | user-data paths under `$REGIN_DATA_DIR` (default `~/.local/share/regin/`) |
| `capture_assistant_response`, `assistant_response_max_bytes` | shared | `true`, `50_000` — controls capture of assistant text in trace ingest |
| `experimental_conceal` | shared | `false` — gates the pattern-conceal experiments surface |
| `topic_proposal_external_agents` | local | `{}` — named external agents for topic proposal runs (see [docs/topics/proposals.md](docs/topics/proposals.md)) |

The authoritative schema is `Settings(BaseSettings)` in `lib/settings.py`.

Provider capability metadata is exposed via `GET /api/providers` and
included in `regin doctor`.

## Patterns (procedure guides)

A **pattern** is a `SKILL.md` markdown file under `$PATTERNS_DIR`
(default `~/.local/share/regin/patterns/<slug>/SKILL.md`) describing one
recurring implementation shape — a controller, a repository, a
migration test, whatever convention you want the agent to follow.

A fresh install ships no patterns. Add them through one of:

- **Web UI** — `/patterns/new` creates a blank `SKILL.md` with the slug,
  title, and `manual: true` frontmatter, plus stub *Disciplines* and
  *Anti-Patterns* sections. Edit the body in your editor of choice.
- **Import** — `lib/patterns/pattern_importer.py` accepts a versioned
  skill bundle (typically downloaded from a regin-skillhub server) and
  unpacks it into `$PATTERNS_DIR`.

Once a pattern exists, regin tracks it in `pattern_docs`, auto-tags it
via `lib/tags/tag_manager.py`, and deploys it as a Claude skill via
`lib/skills/skill_deployer.py`. Invocations are observed via the `Skill`
tool / slash-command hooks (see *Skill Invocation Tracing* below), so the
trace layer can show which patterns the agent actually consulted.

### Frontmatter conventions

```yaml
---
title: "Human-readable title"   # live — PatternDoc.title column
procedure: <slug>               # live — the pattern slug
manual: true                    # marker, written but not read back
source_repos: [imported]        # bundle-export metadata only
imported_at: "<ISO timestamp>"  # informational stamp, never read
---
```

Only `title` and `procedure` feed regin's data model: `title` is a
`PatternDoc` column and `procedure` carries the slug. The rest are
written on create/import but not consumed by regin core:

- `manual: true` marks a pattern as user-authored (vs auto-generated,
  which regin no longer does), but nothing reads it back to gate
  deployment.
- `source_repos` and `imported_at` are vestigial. The old `source_repos`
  DB column no longer exists, so `source_repos` now only
  round-trips through skill-bundle export — the promoter copies it into
  the bundle manifest. `imported_at` is a human-readable stamp the
  importer writes and no code consumes.

The body is free-form markdown; the deployer rewrites only the frontmatter
(to `name` + `description`) and keeps the body inline in the deployed `SKILL.md`.

## Rule engines

A `RuleEngine` (see `lib/rule_engines/base.py`) is the seam by which regin plugs in lint/check tooling. Built-in adapters are `GritEngine` (`grit.py`), `BundleEngine` (`bundle.py`), and `RadonEngine` (`radon_engine.py`); new adapters slot in next to them without touching the sync engine, blueprint, or hook handler.

Configure engines via `settings.rule_engines` — an empty list + an empty `grit_dir` means no engines, no `grit-rules` auto skill, and no PostToolUse enforcement.

```json
{
  "rule_engines": [
    {"id": "grit", "kind": "grit",
     "grit_dir": "~/.local/share/regin/grit",
     "language_ids": ["java"]}
  ]
}
```

### Grit engine — layout

Grit rules live under `<grit_dir>/patterns/<language>/*.grit` (default `~/.local/share/regin/grit/patterns/java/`). The engine owns this convention; no regin core module hardcodes it. Full index in `<grit_dir>/RULES.md`.

### Language routing

Before any rule runs, the PostToolUse handler (`hook_manager/handlers/rule_check.py`) decides which engines claim an edited file: an engine applies when the file's extension matches one of its `language_ids`. The extensions for a language id resolve from the first non-empty source among — a repo-local overlay, the global `settings.language_extensions`, the `lib/languages/` registry, then a fallback map in the handler.

So an existing engine can be pointed at a language `lib/languages/` doesn't ship **without code**: declare the extensions in `settings.language_extensions` and list the id in the engine's `language_ids`.

```json
{
  "language_extensions": {"kotlin": [".kt", ".kts"]},
  "rule_engines": [
    {"id": "grit", "kind": "grit",
     "grit_dir": "~/.local/share/regin/grit",
     "language_ids": ["python", "kotlin"]}
  ]
}
```

#### Per-repo overlay

A registered repo may carry `<repo>/.regin/config.json` (`lib/repo_config.py`) to extend routing for edits **inside that repo only**; commit it in that repo to share the routing with your team. Its `language_extensions` merges over the global map (repo keys win), so one repo can route an engine to a language the rest don't use:

```json
{"language_extensions": {"kotlin": [".kt"]}}
```

The file is read on the edit hot path and cached by mtime; a missing or malformed file is ignored (and logged) rather than breaking the hook. The overlay covers `language_extensions` only — `rule_engines` is not repo-overridable, since a repo-supplied engine list (e.g. a bundle runner) would be an untrusted code-execution surface.

### Trigger matching

Rules declare triggers via `@rule triggers=` comments, indexed in `<grit_dir>/rules.json`. Matching is AND across kinds:
- **Filename globs** (`*Entity.java`): basename must match
- **Content triggers** (`@Entity`): file must contain
- Both must pass if both are declared

The matcher lives in `GritEngine.applies_to` — `scripts/filter_grit_output.py` and `scripts/find_applicable_files.py` ship a standalone copy because they run inside the deployed grit-rules skill without access to regin.

### Post-edit hook

Install from the web UI Settings page, or manually add to your active
provider hook settings file (Claude default: `~/.claude/settings.json`):

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Edit|Write|MultiEdit",
      "hooks": [{
        "type": "command",
        "command": "<path>/.venv/bin/python -m hook_manager PostToolUse",
        "timeout": 60
      }]
    }]
  }
}
```

The `hook_manager` dispatcher invokes `hook_manager.handlers.rule_check.handle`, which iterates every configured rule engine and runs the applicable one.

### Manual checks

```bash
scripts/check_grit.sh <repo-path>              # all rules
scripts/check_grit.sh <repo-path> <rule-id>    # single rule
```

## Frontend Stack

- **Vue 3** (Composition API, `<script setup>`) + **Vue Router**
- **Vite** (dev server + production build)
- **Tailwind CSS v4** (via PostCSS)
- **marked** (client-side markdown rendering)
- Views, shared components, and composables under `frontend/src/`
  (`useFlash` / `useConfirm` are the most-used composables)
- Playwright E2E suite in `frontend/tests/*.spec.js`

### Development

```bash
.venv/bin/python cli/regin.py serve      # Flask API on :8321
cd frontend && npx vite                         # Vite dev server on :5173 (HMR, proxies /api to :8321)

# E2E tests (requires both servers running)
cd frontend && ./node_modules/.bin/playwright test
```

## Skill Invocation Tracing

regin records which skills a session actually used, in the `skill_reads` table,
via three disjoint signals — each a separate hook posting to `POST /api/skill-reads`:

| `source` | Hook | Fires when |
|---|---|---|
| `launch` | `hook_manager.handlers.skill_launch` — `PostToolUse` on the `Skill` tool | the assistant invokes a skill via the native `Skill` tool |
| `invoke` | `hook_manager.handlers.skill_invoke` — `UserPromptExpansion` | the user types a `/slash` command |
| `read` | `hook_manager.handlers.skill_read` — `PostToolUse` on `Read` of `<provider>/skills/*/content.md` | a legacy `content.md` is read (see below) |

`launch` + `invoke` cover every modern invocation, because the native `Skill`
tool loads the skill body on invocation and that call is observable via
`PostToolUse`.

### Single-file SKILL.md

Deployed skills are a **single self-contained `SKILL.md`**: the regin frontmatter
is rewritten to the provider format (`name` + `description`) and the full guide
body is kept inline. `deploy_pattern_as_skill` and `deploy_rules_index_skill`
(`lib/skills/skill_deployer.py`) both produce this shape.

> **History — why no more shim.** Earlier, every skill was split into a thin
> `SKILL.md` shim plus a companion `content.md`, on the assumption that providers
> inject the whole `SKILL.md` into the prompt with no way to observe consultation;
> forcing a `Read content.md` made it an observable tool call. Two things retired
> that: (1) the native `Skill` tool now loads the body on invocation and fires an
> observable `PostToolUse` (captured by `skill_launch`), and (2) measurement showed
> the shim's "read `content.md` first" pointer was skipped ~50% of the time, so the
> real guidance never reached the model. The single-file form fixes that disclosure
> gap while keeping invocation observable.

### content.md back-compat

regin no longer writes `content.md`, but the **read path is retained**: the
`skill_read` hook, the provider `skill_id_from_read_path` helpers, and the
importer/promoter bundle format still recognise a `content.md` when present, so
already-deployed skills and externally-authored bundles keep working until
redeployed. Drift detection (`_deployed_body` in `lib/skills/skill_sync.py`) reads
`content.md` if present and falls back to the `SKILL.md` body otherwise.

### Dashboard

The **Trace** menu in the Vue SPA has a **Skill Reads** tab (`/trace/skill-reads`) showing:
- Per-session stats (reads, distinct skills, linked plan if any)
- Per-skill summary (total reads, last seen)
- Recent read events with links to skills

## Turn & Token Tracing

> Deep-dive docs live in [`docs/trace/`](docs/trace/): see
> [`SPAN_DESIGN.md`](docs/trace/SPAN_DESIGN.md) for the span data
> model, projection pipeline, invariants, and a debugging cookbook,
> and [`TURN_USAGE.md`](docs/trace/TURN_USAGE.md) for per-API-call
> token accounting.

Each `UserPromptSubmit` / `Stop` / `SessionEnd` hook fires
`hook_manager.handlers.turn_trace`, which re-reads the session's
transcript JSONL from the active provider transcript directory (Claude
default: `~/.claude/projects/*/<trace_id>.jsonl`) via
`lib/trace/transcript_usage.py`. It emits a `turn` span (carrying the
latest assistant entry's model, e.g. `claude-opus-4-7[1m]`) and POSTs
one row per assistant API call into the dedicated `turn_usage` table,
keyed `(trace_id, turn_uuid)` for idempotent re-emits.
`lib/trace/trace_service/` rolls those rows up into the `sessions` row
as `peak_context_tokens` / `context_window_tokens`; the window is
resolved by `lib/tokens/model_windows.py::infer_window(model,
peak_tokens)`, which understands variant suffixes like `[1m]`. The full
data model, idempotency rules, and attribution math live in
[`TURN_USAGE.md`](docs/trace/TURN_USAGE.md) and
[`USAGE_ATTRIBUTION.md`](docs/trace/USAGE_ATTRIBUTION.md).

### Dashboard

- **Sessions** list (`/trace/sessions`) shows context usage as
  `peak / window` with a progress bar.
- **Session trace** detail (`/trace/sessions/<trace_id>`) plots per-turn
  context usage plus model/tool metadata.

## Plan Mode Session Tracing

`hook_manager.handlers.plan_trace` runs on `PostToolUse` and tracks
session→plan associations via two attributable paths.

### How It Works

**1. Attributable — `ExitPlanMode` with plan text (Codex-style payload)**

When `ExitPlanMode` carries plan content in its payload, the handler
writes the plan to `<provider plans dir>/` under a deterministic filename
that embeds the session prefix (e.g. `claude-plan-<prefix>-<timestamp>.md`),
emits a `plan.write` span tagged with `plan_filename`, and records the
session→plan mapping in the `plan_sessions` table via a POST to
`/api/plan-sessions`.

**2. Attributable — direct plan-file edits (`Write` / `Edit` / `MultiEdit`)**

When the agent writes or edits a file whose path lives inside `<provider
plans dir>/`, the handler detects it, emits a `plan.write` (on `Write`)
or `plan.update` (on `Edit`/`MultiEdit`) span, and records the
session→plan mapping in `plan_sessions` the same way.

### Dashboard Correlation

- **Session trace** shows `plan.exit` boundary markers, tagged with the plan filename only on the attributable `ExitPlanMode`-with-text path (otherwise a bare marker).
- **Sessions list** joins `plan_sessions` to surface the associated plan name alongside each session; the `plan_sessions` table is the read-optimised cache populated on attributable plan touches.

## Agent Messages (send_to_user inbox)

`send_to_user` is an MCP tool an agent calls to push a message at the user mid-task — a progress update, a partial result, or a blocker that needs eyes. It is the agent→human channel; distinct from `lib/trace/` (which records what the agent *did*).

**Why the hook is the writer, not the MCP server.** A stdio MCP server is *session-blind* — it never learns which Claude Code session invoked it. The PostToolUse hook does. So the server (`lib/agent_messages/mcp_server.py`) only declares the typed parameter schema and acknowledges; the hook (`hook_manager/handlers/post_tool_trace._record_agent_message`) reads the tool input, knows the session/agent/span, and writes the durable row via `lib/agent_messages/store.py`. The hook also pins the tool span's `span_id` up front so the message can deep-link back into the trace.

| Piece | Role |
|-------|------|
| `lib/orm/models/agent_messages.py` | `AgentMessage` model + `MESSAGE_TYPES` severity ordering (`progress < note < lesson < result < summary < warning < blocker`) |
| `lib/agent_messages/store.py` | The only writer: insert, **supersede-by-`key`** (a keyed message updates in place — "building… 40%" → "done" stays one card), list, inbox, unread count, read/ack/dismiss |
| `lib/agent_messages/webhook.py` | Opt-in outbound POST for messages at/above `settings.agent_messages.webhook_min_severity` (off unless `webhook_url` is set) |
| `web/blueprints/trace/agent_messages.py` | `/api/sessions/<id>/agent-messages` (per-session feed, with legacy span fallback) + `/api/agent-messages/{inbox,unread-count,read,<id>/ack,<id>/dismiss,<id>/pin}` |
| `frontend/src/views/InboxView.vue` + `components/InboxMessageCard.vue` | Cross-session **Inbox** with a live unread **badge** (`composables/useInboxUnread.js`); the per-session **Messages** tab in `SessionTraceView.vue` renders the same rows |

The store is the **canonical** record — not reconstructed from `session_spans` at read time, so a dropped span can't make a message vanish. Unlike `session_spans` it is *mutable* (read/ack/dismiss timestamps change after insert), so it does not follow the append-only span convention. The `agent_messages` DDL lives in `db/schema.sql`.

**Webhook latency note:** dispatch is synchronous inside the hook, so a configured webhook adds its round-trip (≤ `webhook_timeout_seconds`) to the hook return — but only for messages that clear the severity gate (warning/blocker are rare), and only when a webhook is configured at all.

A message of type `lesson` is additionally teed into the agent-memory store (next section) — `send_to_user(type=lesson)` is the explicit capture endpoint for cross-session experience.

## Agent Memory (cross-session experience)

`lib/memory/` learns from past sessions and surfaces that experience into future ones. The lifecycle is **capture → consolidate (`reflect`) → recall → reinforce**; `send_to_user(type=lesson)` is one capture endpoint into the system (see *Agent Messages* above), not the system itself.

**Separate, self-initializing database.** Memory lives in its own SQLite file (see *Agent memory database* above), wired as the third instance of the multi-engine pattern: `get_memory_engine()` / `MemorySessionLocal()` in `lib/memory/engine.py` reuse `lib/orm/engine._build_engine` (WAL pragmas, busy timeout). The models declare their **own** `MetaData` (`lib/memory/models.py`) — the explicit `metadata = MetaData()` assignment is load-bearing: SQLModel subclasses otherwise share the global metadata, and `create_all(memory_engine)` would build regin's entire schema into the memory file.

**Ports, not providers.** The engine depends on four Protocols in `lib/memory/ports.py` — `EmbeddingProvider`, `LLMProvider`, `MemoryStore`, `MemorySink` — and every port degrades gracefully: no embedder → FTS-only recall; no LLM → reflect still dedups (text-ratio) but skips contradiction judging *and synthesis*, and distill proposes nothing (and never supersedes) (the LLM *is* the abstraction step — heuristics alone can detect signal but not turn it into a reusable rule); no sink → no outbound export (the default). Concrete adapters (SkillRouter embeddings, an external-agent LLM command) live only in `lib/memory/adapters.py` and are injected at the edge; removing any of them is a zero-diff change to the engine.

| Piece | Role |
|-------|------|
| `lib/memory/__init__.py` | Facade: `remember / recall / reflect / get / update / forget / supersede / stats` over a lazily-constructed default store |
| `lib/memory/store.py` | SQLite `MemoryStore`: one `memories` table carrying both tiers (`working` → `episodic`) plus `memory_embeddings` / `memory_validations` / `injection_events` side tables; recall is FTS5 + dense + RRF + cross-encoder rerank — the `pattern_router` pipeline shape against the memory corpus — then **quality-weighted**: a bounded `[0.9, 1.3]` `_quality_factor` (importance · veracity · deliberate-recall count · recency half-life) re-ranks the relevance order so a sharp, proven memory beats a mundane one of equal lexical match without ever overriding relevance (`recall_quality_weighting`) |
| `lib/memory/reflect.py` | Consolidation: near-duplicate dedup (embedding cosine, or text-ratio fallback), LLM-judged contradictions in the similarity gray zone, working→episodic promotion with `recall_count`-driven importance reinforcement, **synthesis** — clusters of *related but distinct* episodic rows (cosine in `[0.55, dedup_threshold)`) are handed to the LLM to abstract one higher-order rule per cluster (Generative-Agents reflection, the step past dedup/GC; needs both an embedder and an LLM, `synthesis_enabled`; sources are kept and marked `synthesized` so the pass is idempotent), content-hash-skipped embedding, **forget-stale** — episodic rows aged past `forget_after_days` with `recall_count==0` are retired (the negative half of the usefulness loop: speculative inject doesn't reinforce, so a long-aged never-recalled row never earned its keep), and **decay** — a never-positively-validated episodic row that either drew `decay_ignored_threshold` (default 5) feedback `ignored` verdicts or was auto-injected `decay_injected_threshold` (default 8) times without one reinforcement loses 0.1 importance per run (floored at 0.1, never retired from this signal; either threshold at 0 disables that half). The injection-volume trigger reads `injection_events` directly, so the negative loop stays alive even for the common session that never triggers a grade (the `ignored`-validation trigger fires only at grade time) |
| `lib/memory/distill.py` | Implicit capture from a finished session, **LLM-only by contract** — the model is the abstraction step. **Agentic** (`resolve_distiller` grants the read-only `trace dump`/`trace span` commands): the distiller is handed the trace id + the high-signal hints (the *grader findings* and *Notable signals* tags) and **self-fetches** only the spans it needs — the raw trace is never folded into the prompt, so size stays constant with session length (same scaling fix as the deep-tier judge). Heuristic detectors (failure→fix chains, user corrections) only surface as hints, never write proposals directly (that produced "running-account" noise); the prompt carries a BAD/GOOD few-shot demanding the reusable **rule**, not the episode. Each draft is schema-validated (required rule-shaped title, body ≥ 60 chars) and **self-scores `importance` in [0,1]**; `_finalize_status` then **drops** it below `distill_min_importance`, **auto-approves** (`status='active'`) at/above `auto_approve_importance`, or **queues** it (`status='proposed'`) for human review in the gray band. Returning `[]` is legitimate; no LLM configured → nothing proposed. Before writing, each draft is reconciled against existing memories: a near-duplicate **reinforces** the existing row instead of inserting (dedup-at-write), and a draft the LLM judges to **contradict** one (same-topic, incompatible claim in the lexical gray band) **supersedes** it — the old row retired `veracity=false`, `distill_supersede_on_conflict` — the immediate, lexical complement to reflect's batch embedding-based gray-zone check. Auto-resolves the session's repo scope via `session_repos` and stamps `distill`/`llm` provenance tags |
| `lib/memory/scoping.py` | Scope policy wrapper (`global` / `per-repo` / `per-repo-tagged`, default `per-repo-tagged`: repo-stamped writes, globally visible recall); the store only ever sees opaque scope strings |
| `lib/memory/mcp_server.py` | The `recall` MCP tool for deeper mid-task pulls; the server process is session-long-lived, so the dense + rerank legs are affordable there |
| `hook_manager/handlers/memory_recall.py` | UserPromptSubmit auto-inject: routes eligible prompts through recall and returns a `<recalled_experience>` block. Recall defaults to borrowing the warm `regin serve` process for the full dense + rerank pull (`inject_dense_via_server`: a fresh hook process can't load the embedder, so it POSTs `/api/memory/recall` over loopback — granted a loopback-only auth exemption in `web/app.py` — with a short timeout and a clean fall back to in-process FTS-only when the server is down). Speculative surfacing never reinforces and is overlap-gated (`inject_min_overlap` distinct content tokens — BM25 always ranks *something*, so an ungated inject attaches tangential memories to every prompt as the store grows). Same-session deduped (`inject_dedup_session`, tracked in `injection_events`) so the same memory isn't re-rendered every turn; slash commands recall on their argument text, not the bare `/command`. When it injects, it records a `memory.recall` span (rendered block + per-hit metadata, gated by `agent_memory.trace_recall`) so the trace shows exactly what was fed to each prompt — see *Session trace* below. A routed `<topic_context>` is recorded in `topic_injections`, and a route graded irrelevant too often is *proposed* for suppression but withheld only on a human-approved decision (`_topic_suppressed` reads `topic_route_decisions`) — see the **Topic-routing feedback loop** under *Session Grader* |
| `web/blueprints/memory.py` + `frontend/src/views/MemoryView.vue` | `/api/memory/*` + the **Memory** view: list/edit, approve proposals, retire/forget, recall probe, run-reflect |
| `cli/commands/memory.py` | `regin memory {recall,list,stats,reflect,distill,approve,forget}` |

**Capture paths** both land through the store's `remember`: the PostToolUse hook tees `type=lesson` messages in with span/agent/scope provenance and a `send_to_user` tag (`post_tool_trace._remember_lesson`, guarded so neither store's failure blocks the other), and `regin memory distill <session>` proposes from the trace. **Reinforcement** is asymmetric on purpose: deliberate pulls (MCP tool, CLI, API probe-less recalls) bump `recall_count` (which reflect folds into importance); speculative auto-inject does not — *except* a memory injected earlier in a session that matches again on a later prompt is reinforced once (`reinforce_resurfaced`), since repeated relevance across a session is an earned usefulness signal rather than a one-off speculative surface.

Memory rows are **mutable by design** — updated, superseded (`superseded_by` chains), retired, deleted — the opposite of the append-only `session_spans` convention; curation is the point. Settings live under `settings.agent_memory` (enabled flag, DB path, auto-inject knobs, scope policy, dedup thresholds, the distill self-score band `distill_min_importance` / `auto_approve_importance`, at-write conflict resolution `distill_supersede_on_conflict`, reflect synthesis `synthesis_enabled`, recall quality weighting `recall_quality_weighting` / `recall_recency_half_life_days`, the `forget_after_days` stale-retirement window, and the decay triggers `decay_ignored_threshold` / `decay_injected_threshold`).

## Session Grader (post-hoc rubric grades)

`lib/grader/` grades a *completed* session's trace on two independent axes that are deliberately **never fused into one number**: `correctness` (did the agent's claims hold up against the evidence?) and `process` (was the trajectory efficient?). The core re-framing: every resolved `tool_use` span and its recorded output is a citable source, so the grader's unit of work is *"for every assertion the agent made, find the span that backs it — and judge the link, not the assertion."*

**Correctness axis** — a three-criterion pipeline over a typed **claim ledger** extracted from the final deliverable (`extraction.py`): each claim is typed `state` / `result` / `external` / `diagnostic`, which selects its grounding bar (`grounding.py`: a `state` claim needs a Read/Edit span showing the cited code, a `result` claim needs a Bash span whose command matches and status confirms — "tests pass" with no run is `UNGROUNDED`, a failed run is `CONTRADICTED`, and a run that *predates a later edit* is `STALE` — the timeline rule only regin's timestamped spans make checkable). `coverage.py` checks a required-items checklist derived from the *task alone before grading* (anti-gaming: the agent can't define coverage down), and `source_quality.py` classifies each grounding source authoritative-vs-proxy (a README read or a grep pattern-match is a `PROXY` for a code-behavior claim; Q&A/blog domains are proxies for external facts). Gates fire before ratios: a load-bearing `CONTRADICTED` claim ⇒ `fail`; any `MISSING` item, not-`GROUNDED` claim, or proxy-backed load-bearing claim ⇒ at most `needs_revision`. Every ledger gets the synthetic load-bearing claim `c0` ("the session accomplished the task"), grounded by the checklist, so terse code-only sessions still give the gate something to bite.

**Process axis** (`process.py`) — grades trajectory *properties*, never a prescribed step list: P1 tool-use appropriateness (`SUBOPTIMAL` shell `cat`/`grep` where dedicated tools exist; `WASTED` reads whose output fed nothing downstream), P2 redundancy (re-reads of an unchanged target, ≥K same-shape failing commands with no intervening edit), P3 reliability (errors recovered vs ignored; an ignored error feeding a load-bearing claim caps the verdict at `acceptable`), and P4 cost-proportionality (cost percentile against other captured sessions of the same task class, cost-per-covered-item, and the cache-read-share context-bloat sub-check — the part generic graders can't see because they don't have regin's token split). The axis is *conditioned* on the correctness verdict (high spend on a correct session is proportionate, not wasteful) but never merged with it; the cross-axis aggregate is `pareto.py`'s cost-per-correct-outcome analytics, which flags *cheaply-wrong* and *expensively-right* sessions as the off-frontier cases worth a human look.

**Two-tier cost strategy** (`service.py`): the `screen` tier is fully mechanical — no LLM, deterministic, cheap enough to run on everything. The `deep` tier injects an external judge agent (same subprocess `LLMProvider` contract as memory distill; `settings.grader.external_agent` names a key in `topic_proposal_external_agents`) for claim extraction, a completeness-critic second pass, checklist derivation, and grounding rescue — with two anti-gaming guards baked in: the **verbatim-provenance guard** (an extracted claim whose `raw_text` isn't a substring of the artifact is dropped — the extractor can't invent claims) and the **anti-paraphrase guard** (a judge rescue must quote verbatim from the span excerpt or its answer is discarded — the agent's restatement of a tool result is never evidence). `tier="auto"` screens first and escalates only borderline/failing sessions. Grades are **triage, not truth** — they surface suspicious sessions with evidence-cited bullets for human spot-checking, with tier/judge/rubric provenance stamped on every row.

**One combined deep judge** (`combined_agentic.py`): the deep tier runs a **single** self-fetching judge subprocess that grades every requested dimension — correctness, process, and any selected aspects — in one investigation, returning one JSON parsed section-by-section through the existing builders/gates (so the quote-guard and rubric thresholds are unchanged; only the prompt is unified). This is one captured session per grade instead of one `<role>` judge session per axis, plus a single `trace dump --index`. A dimension the judge omits or mangles falls back to the mechanical tier (axes) or is simply absent (aspects, which are LLM-only). The per-run dimensions are chosen by the caller — `grade_session(axes=…, aspects=…)`, the API `axes`/`aspects`, the CLI `--axis`/`--aspect` — and a run needs at least one axis *or* aspect. **Gradeable aspects** are reviewer-defined dimensions (`settings.grader.aspects`, non-builtin) graded holistically: the judge returns a `satisfied`/`needs_revision`/`fail` verdict with span-cited findings (hallucinated span_ids dropped), stored under the aspect key like any axis. The builtin aspects (`correctness`/`process`) mirror the grounded axes and are graded as axes, never as generic aspects.

**Grade→memory loop** — a grade isn't a dead-end verdict; it feeds the systems that improve future runs. Two stages, both reusing the [Agent Memory](#agent-memory-cross-session-experience) rail rather than a parallel store: (1) *per-session* — when a persisted, non-test grade flags any axis (verdict ≠ `satisfied`/`efficient`), `service.py` hands the grade's findings (non-`GROUNDED` claims with referents, `MISSING` coverage, `WASTED` spans) to `lib/memory/distill.py` as the highest-priority candidates, so the durable rule behind each problem becomes a recallable lesson (with a `distill_importance_bonus` since a grade is independent corroboration). Distilling is a **per-run decision** — `grade_session(distill=…)`, the CLI `--distill/--no-distill`, and the API `distill` flag opt in or out; the UI checkbox defaults off, and an unset flag falls back to `settings.grader.distill_on_fail`. (2) *cross-session* — `lib/grader/aggregate.py` buckets every failing session's problems into stable **mode keys** (`failure_modes.py`: `claim:state:UNGROUNDED`, `coverage:MISSING`, `process:WASTED`, …); a mode recurring across ≥ `settings.grader.aggregate_min_sessions` distinct sessions is consolidated into one idempotent lesson (refreshed in place via a `grade-aggregate:<mode>` tag) carrying the rule plus its remediation. Both land `proposed` (human-gated); the loop is `needs_revision → distilled lesson → recalled as <recalled_experience> next matching session → better next grade`.

**Topic-routing feedback loop** — the auto-inject hook's `<topic_context>` banner (`topic_route_inject`) was otherwise fire-and-forget: it routed a prompt through the topic graph and never learned whether the route fit. A **gradeable aspect** closes that loop as the *outcome* signal the deterministic engagement proxy (`feedback.py`, referent overlap) structurally can't produce. With `topic_relevance_feedback` on: (1) every injected topic is recorded in `topic_injections` (the topic analog of `injection_events`); (2) at grade time `service.py` stamps the verdict of the aspect named by `topic_relevance_aspect` (default `injectedrelated` — a reviewer-defined aspect that judges whether the injected topics/memories actually fit the user's goal, citing the `memory.recall` span) onto that session's rows; (3) a topic whose scored injections clear `topic_relevance_min_scored` *and* a fail rate ≥ `topic_relevance_fail_rate` is surfaced as a **suppression proposal** — but it keeps routing. **Withholding is human-gated**, the same precision-first `proposed → approved` contract every memory write goes through: `_route_topic` withholds a route only when a human has written a `suppressed` decision in `topic_route_decisions` (`store.set_topic_decision`; `allowed` pins a route on / rejects a proposal; no row = `auto`, routes and stays re-proposable). The gate is driven from the CLI (`regin memory topic-decide <topic> suppress|allow|auto`), the API (`POST /api/memory/topic-feedback/<topic>/decision`), and the **Topic routing feedback** panel in the Memory view (status + approve/keep-routing/reset buttons); `regin memory topic-feedback` lists the per-topic stats and status. So a proposal isn't invisible until someone opens that panel, each one is pushed to the [agent inbox](#agent-messages-send_to_user-inbox) as a `warning` (`topic_relevance_notify`, `lib/grader/topic_notify.py`): one durable card per topic under a synthetic session so the keyed supersede dedups it across grading sessions, resolved (dismissed) when a decision is made. The whole loop is best-effort: a feedback fault never costs a useful route or fails a grade.

| Piece | Role |
|-------|------|
| `lib/grader/rubric.py` | Rubric-as-data (criteria, verdicts, bars, gates) + `RUBRIC_VERSION` stamped onto every grade |
| `lib/grader/evidence.py` | Trace → evidence index: reads/edits/bash/fetches by target with timeline order, prompt + final assistant text |
| `lib/grader/extraction.py` → `correctness.py` | Claim ledger → groundedness/coverage/source-quality → gates → the mandated scoreboard-then-bullets report |
| `lib/grader/process.py` + `pareto.py` | P1–P4 trajectory grading + the cost-per-correct-outcome analytics layer |
| `lib/grader/combined_agentic.py` | the single deep judge: one investigation → per-dimension verdicts (axes + gradeable aspects), parsed via the axis builders |
| `lib/grader/store.py` | `session_grades` in the primary DB — append-only; readers take the latest row per (trace, axis) |
| `lib/grader/failure_modes.py` + `aggregate.py` | Grade `detail` → stable mode keys → cross-session consolidation into agent-memory lessons |
| `web/blueprints/grades.py` + `frontend/src/views/GradesView.vue` | `/api/grades*` + the **Grades** view (per-dimension verdict badges, expandable reports, grade-now with per-run axis + gradeable-aspect selection and a distill opt-in) |
| `cli/commands/grader.py` | `regin grade {run,show,list,pareto,reflect}` |

Settings live under `settings.grader` (`enabled`, `external_agent`, `auto_escalate`, `deep_max_claims`, `distill_on_fail`, `distill_importance_bonus`, `aggregate_min_sessions`); the numeric bars are rubric data in `rubric.py`, versioned with the rubric rather than the deployment.
