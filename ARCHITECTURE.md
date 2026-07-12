# Architecture

System internals for developers working on regin itself. For setup and usage, see [README.md](README.md).

This doc is the **stable map + boundaries**: module layout, database, config, auth, and the frontend stack inline below. The deepest subsystems (rule engines, agent memory, the grader, agent messages) delegate their *mechanism* to a per-subsystem **topic wiki** under `.regin/topics/wiki/` — git-tracked, human-ratified through topic review, and browsable in the app's **Topics** view. Those sections keep the entry points and contracts here and link the full walk-through there, so one fact has one home.

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
| `lib/orm/models/` | Typed tables grouped by domain: `users.py` (accounts, audit log), `sync.py` (repos, branches), `patterns.py` (pattern docs, tags, deployments), `rules.py` (rule triggers, experiments), `trace.py` (session spans/sessions, turn usage, skill reads, plan sessions, prompt images), `proposals.py` (topic proposal runs/revisions, graph snapshots, feedback), `prompts.py`, `payload_schema_drift.py`, `agent_messages.py` (inbox messages), `grades.py` (session grades) |
| `lib/logging_setup.py` | `configure_logging()` + `get_logger()` using structlog |
| `lib/providers/` | Provider adapters (`claude`, `codex`, `generic`, `kimi`) for skills/hooks/session path conventions and capability gating |
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
| `experimental_providers` | local | `false` — when true, surfaces `codex`, `generic`, and `kimi` in the settings UI and `/api/providers` |
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

A `RuleEngine` (`lib/rule_engines/base.py`) is the seam by which regin plugs in lint/rewrite tooling. Built-in adapters are `GritEngine` (`grit.py`), `BundleEngine` (`bundle.py`), and `RadonEngine` (`radon_engine.py`); new adapters slot in next to them without touching the sync engine, blueprint, or hook handler. Configure engines via `settings.rule_engines` — an empty list + an empty `grit_dir` means no engines, no `grit-rules` auto skill, and no PostToolUse enforcement.

On every edit the PostToolUse handler (`hook_manager/handlers/rule_check.py`) routes the file to the engines whose `language_ids` claim its extension; the extensions for a language id resolve from the first non-empty source among a repo-local `<repo>/.regin/config.json` overlay, the global `settings.language_extensions`, the `lib/languages/` registry, then a handler fallback — so an existing engine can be pointed at a new language **without code** (the repo overlay extends `language_extensions` only, never `rule_engines`, which would be an untrusted code-execution surface). Install the hook from the web UI Settings page; run rules manually with `scripts/check_grit.sh <repo-path> [<rule-id>]`.

Deep dive — the adapter Protocol, the three-source registry precedence, the Grit/Bundle/Radon adapters, `@rule triggers=` matching, the in-tree `example/rule/` bundles, and the add-an-engine / add-a-language playbook: **[`.regin/topics/wiki/rule-engine-design.md`](.regin/topics/wiki/rule-engine-design.md)**.

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

`send_to_user` is the agent→human MCP channel — a mid-task progress update, partial result, or blocker that needs eyes; distinct from `lib/trace/` (which records what the agent *did*). Because a stdio MCP server is *session-blind*, the server (`lib/agent_messages/mcp_server.py`) only declares the typed schema; the PostToolUse hook (`post_tool_trace._record_agent_message`) — which knows the session/agent/span — writes the durable row via `lib/agent_messages/store.py`, the **sole writer** (insert, **supersede-by-`key`** so one advancing card replaces itself, inbox, unread count, read/ack/dismiss) and pins the span up front for deep-linking.

The `agent_messages` store is **canonical and mutable** — not reconstructed from `session_spans`, so a dropped span can't make a message vanish, and read/ack timestamps change after insert (deliberately unlike the append-only span convention; DDL in `db/schema.sql`). High-severity messages fan out to pluggable **push channels** (`lib/agent_messages/push/`: `webhook.py`, `telegram.py`, `lark.py` behind the `PushChannel` contract) via `registry.maybe_dispatch`. A `type=lesson` message is additionally teed into [Agent Memory](#agent-memory-cross-session-experience) — the explicit cross-session-experience capture endpoint. The cross-session **Inbox** (`InboxView.vue`, live unread badge via `useInboxUnread.js`) and the per-session **Messages** tab render the same rows.

Deep dive — the declared notification **event bus** (`events.py`: permission/plan/drift/grade/suppression events published through one `emit` to the same writer with a deep-link URL), the opt-in interaction-event pushes, and the push severity gating: **[`.regin/topics/wiki/agent-messages-inbox.md`](.regin/topics/wiki/agent-messages-inbox.md)**.

## Agent Bridge (human/system → live agent)

The inverse channel of Agent Messages above: instead of the agent pushing to a human, an external sender (a phone, `curl`, another agent) pushes a message *into* a live `claude` session as if typed at its prompt. See `docs/agent-bridge-design.md` for the full design and `.regin/topics/wiki/live-session-mobile-card.md` for how the `/live` card consumes it.

**Transport is guarded tmux keystroke injection**, not a hook — hooks are session-blind while the agent idles at its prompt, exactly when delivery matters most. `lib/agent_bridge/delivery.py` types into the session's registered tmux pane (`send-keys -l --`) behind several fail-closed guards: pane-identity re-verification (server pid + pane pid must match the SessionStart-registered triple), a target-process allowlist (refuses a pane that fell back to a shell, closing the injection→shell-execution escalation), copy-mode cancel, control-byte/newline sanitization, and a capture-pane ack before Enter.

**Storage:** `bridge_panes` (the session → pane registry, written by a SessionStart hook) and `bridge_messages` (append-only inbox: sender, body, delivery outcome), both in `db/schema.sql`; `lib/agent_bridge/store.py` is the sole writer, mirroring `lib/agent_messages/store.py`.

**HTTP surface** (`web/blueprints/bridge.py`) is two credential tiers: `POST /api/bridge/messages` + `GET /api/bridge/{sessions,messages}` are bearer-token-guarded (`settings.agent_bridge.token`, a credential separate from the web-UI JWT) for headless/external callers; the session-scoped `/api/sessions/<id>/bridge-{send,key,answer,commands,screen}` routes instead ride the app's JWT gate plus `require_editor`, backing the `/live` card's composer, Q&A answer flow, slash autocomplete, and terminal peek — the bridge token itself never reaches the browser.

Gated off by default (`settings.agent_bridge.enabled = False`); per-session delivery additionally requires the pane's registration to opt in.

## Agent Memory (cross-session experience)

`lib/memory/` learns from past sessions and surfaces that experience into future ones — lifecycle **capture → consolidate (`reflect`) → recall → reinforce**; `send_to_user(type=lesson)` is one capture endpoint (see *Agent Messages* above), not the system itself. It lives in its **own self-initializing SQLite DB** (`db/regin_memory.db`, see *Agent memory database* above): `lib/memory/models.py` declares its **own** `MetaData` — load-bearing, so `create_all` / `regin init` / Alembic never build regin's schema into the memory file and accumulated experience survives `rebuild`. The engine depends on four Protocols in `lib/memory/ports.py` (`EmbeddingProvider`, `LLMProvider`, `MemoryStore`, `MemorySink`), each **degrading gracefully** (no embedder → FTS-only recall; no LLM → no contradiction judging / synthesis / distill — the LLM *is* the abstraction step; no sink → no export); concrete adapters live in `lib/memory/adapters.py` and are injected at the edge, so swapping one is a zero-diff change to the engine.

Rows are **mutable by design** — updated, superseded (`superseded_by` chains), retired, forgotten — the opposite of the append-only `session_spans` convention; curation is the point. Reinforcement is asymmetric: deliberate pulls (MCP/CLI/API) bump `recall_count`; speculative auto-inject does not (except an earned same-session resurface). Settings live under `settings.agent_memory`; curation surfaces are `regin memory …` (`cli/commands/memory.py`), `/api/memory/*`, and `MemoryView.vue`.

Deep dives, all under `.regin/topics/wiki/`:

- **Engine, ports/adapters, tiers & self-initializing DB** — [`agent-memory-architecture.md`](.regin/topics/wiki/agent-memory-architecture.md)
- **Recall ranking stack** (FTS + dense + RRF + cross-encoder rerank, `_quality_factor` weighting, topic boost, MMR, edge expansion) — [`memory-recall-pipeline.md`](.regin/topics/wiki/memory-recall-pipeline.md)
- **Consolidation** (`reflect`: mechanical pre-pass, the one agentic *dream* stage, lifecycle decay & forget-stale, embed + edges) — [`memory-consolidation-reflect.md`](.regin/topics/wiki/memory-consolidation-reflect.md)
- **Capture & distillation** (the `type=lesson` tee + the agentic, self-fetching post-session distiller and its self-scored importance band) — [`memory-distillation-capture.md`](.regin/topics/wiki/memory-distillation-capture.md)
- **Auto-injection & on-demand recall** (`UserPromptSubmit` hook borrowing the warm `serve` embedder, the `<recalled_experience>` block, the long-lived `recall` MCP server) — [`memory-auto-injection.md`](.regin/topics/wiki/memory-auto-injection.md)

## Session Grader (post-hoc rubric grades)

> The **eval-grading** topic wiki ([`.regin/topics/wiki/eval-grading.md`](.regin/topics/wiki/eval-grading.md)) is a higher-level overview of this subsystem; the mechanism detail below is the authoritative reference.

`lib/grader/` grades a *completed* session's trace on two independent axes that are deliberately **never fused into one number**: `correctness` (did the agent's claims hold up against the evidence?) and `process` (was the trajectory efficient?). The core re-framing: every resolved `tool_use` span and its recorded output is a citable source, so the grader's unit of work is *"for every assertion the agent made, find the span that backs it — and judge the link, not the assertion."*

**Correctness axis** — a three-criterion pipeline over a typed **claim ledger** extracted from the final deliverable (`extraction.py`): each claim is typed `state` / `result` / `external` / `diagnostic`, which selects its grounding bar (`grounding.py`: a `state` claim needs a Read/Edit span showing the cited code, a `result` claim needs a Bash span whose command matches and status confirms — "tests pass" with no run is `UNGROUNDED`, a failed run is `CONTRADICTED`, and a run that *predates a later edit* is `STALE` — the timeline rule only regin's timestamped spans make checkable). `coverage.py` checks a required-items checklist derived from the *task alone before grading* (anti-gaming: the agent can't define coverage down), and `source_quality.py` classifies each grounding source authoritative-vs-proxy (a README read or a grep pattern-match is a `PROXY` for a code-behavior claim; Q&A/blog domains are proxies for external facts). Gates fire before ratios: a load-bearing `CONTRADICTED` claim ⇒ `fail`; any `MISSING` item, not-`GROUNDED` claim, or proxy-backed load-bearing claim ⇒ at most `needs_revision`. Every ledger gets the synthetic load-bearing claim `c0` ("the session accomplished the task"), grounded by the checklist, so terse code-only sessions still give the gate something to bite.

**Process axis** (`process.py`) — grades trajectory *properties*, never a prescribed step list: P1 tool-use appropriateness (`SUBOPTIMAL` shell `cat`/`grep` where dedicated tools exist; `WASTED` reads whose output fed nothing downstream), P2 redundancy (re-reads of an unchanged target, ≥K same-shape failing commands with no intervening edit), P3 reliability (errors recovered vs ignored; an ignored error feeding a load-bearing claim caps the verdict at `acceptable`), and P4 cost-proportionality (cost percentile against other captured sessions of the same task class, cost-per-covered-item, and the cache-read-share context-bloat sub-check — the part generic graders can't see because they don't have regin's token split). The axis is *conditioned* on the correctness verdict (high spend on a correct session is proportionate, not wasteful) but never merged with it; the cross-axis aggregate is `pareto.py`'s cost-per-correct-outcome analytics, which flags *cheaply-wrong* and *expensively-right* sessions as the off-frontier cases worth a human look.

**Two-tier cost strategy** (`service.py`): the `screen` tier is fully mechanical — no LLM, deterministic, cheap enough to run on everything. The `deep` tier injects an external judge agent (same subprocess `LLMProvider` contract as memory distill; `settings.grader.external_agent` names a key in `topic_proposal_external_agents`) for claim extraction, a completeness-critic second pass, checklist derivation, and grounding rescue — with two anti-gaming guards baked in: the **verbatim-provenance guard** (an extracted claim whose `raw_text` isn't a substring of the artifact is dropped — the extractor can't invent claims) and the **anti-paraphrase guard** (a judge rescue must quote verbatim from the span excerpt or its answer is discarded — the agent's restatement of a tool result is never evidence). `tier="auto"` screens first and escalates only borderline/failing sessions. Grades are **triage, not truth** — they surface suspicious sessions with evidence-cited bullets for human spot-checking, with tier/judge/rubric provenance stamped on every row. The deep tier runs a **single** self-fetching judge (`combined_agentic.py`) that grades every requested dimension — correctness, process, and any selected **gradeable aspects** (`settings.grader.aspects`, reviewer-defined, `satisfied`/`needs_revision`/`fail` with span-cited findings) — in one investigation, parsed section-by-section through the existing builders/gates.

**Grade→memory loop** — a grade isn't a dead-end verdict; it feeds the systems that improve future runs, both reusing the [Agent Memory](#agent-memory-cross-session-experience) rail rather than a parallel store: (1) *per-session* — when a persisted, non-test grade flags any axis, `service.py` hands the grade's findings (non-`GROUNDED` claims with referents, `MISSING` coverage, `WASTED` spans) to `lib/memory/distill.py` as the highest-priority candidates (with a `distill_importance_bonus`), so the durable rule behind each problem becomes a recallable lesson. (2) *cross-session* — `lib/grader/aggregate.py` buckets every failing session's problems into stable **mode keys** (`failure_modes.py`: `claim:state:UNGROUNDED`, `coverage:MISSING`, …); a mode recurring across ≥ `settings.grader.aggregate_min_sessions` distinct sessions is consolidated into one idempotent lesson (refreshed in place via a `grade-aggregate:<mode>` tag). Both land `proposed` (human-gated).

**Topic-routing feedback loop** — a **gradeable aspect** closes the loop the deterministic engagement proxy can't: with `topic_relevance_feedback` on, every injected `<topic_context>` is recorded in `topic_injections`, the aspect judges whether it fit the goal, and a topic whose fail rate clears the thresholds is surfaced as a **suppression proposal** — but keeps routing. **Withholding is human-gated**: `_route_topic` withholds only on a `suppressed` decision in `topic_route_decisions` (driven from `regin memory topic-decide`, the API, and the Memory view's **Topic routing feedback** panel); each proposal is also pushed to the [agent inbox](#agent-messages-send_to_user-inbox) as a `warning`. The whole loop is best-effort — a feedback fault never costs a route or fails a grade.

| Piece | Role |
|-------|------|
| `lib/grader/rubric.py` | Rubric-as-data (criteria, verdicts, bars, gates) + `RUBRIC_VERSION` stamped onto every grade |
| `lib/grader/extraction.py` → `correctness.py` | Claim ledger → groundedness/coverage/source-quality → gates → the scoreboard-then-bullets report |
| `lib/grader/process.py` + `pareto.py` | P1–P4 trajectory grading + the cost-per-correct-outcome analytics layer |
| `lib/grader/combined_agentic.py` | The single deep judge: one investigation → per-dimension verdicts (axes + gradeable aspects) |
| `lib/grader/store.py` | `session_grades` in the primary DB — append-only; readers take the latest row per (trace, axis) |
| `lib/grader/failure_modes.py` + `aggregate.py` | Grade `detail` → stable mode keys → cross-session consolidation into agent-memory lessons |
| `web/blueprints/grades.py` + `frontend/src/views/GradesView.vue` | `/api/grades*` + the **Grades** view |
| `cli/commands/grader.py` | `regin grade {run,show,list,pareto,reflect}` |

Settings live under `settings.grader` (`enabled`, `external_agent`, `auto_escalate`, `deep_max_claims`, `distill_on_fail`, `distill_importance_bonus`, `aggregate_min_sessions`); the numeric bars are rubric data in `rubric.py`, versioned with the rubric rather than the deployment.
