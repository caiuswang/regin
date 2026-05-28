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
| `skill_reads` | Skill-read events from the `Read` PostToolUse hook |
| `plan_sessions` | Durable session→plan mapping; populated on attributable plan touches (see *Plan Mode Session Tracing* below) |

Token counters on `sessions` are part of the baseline schema (`db/schema.sql`, anchored by `alembic/versions/0001_baseline.py`).

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
`lib/skills/skill_deployer.py`. The shim+companion pattern (see *Skill
Read Tracing* below) lets the trace layer observe which patterns the
agent actually consults.

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

The body is free-form markdown; the deployer reads only the frontmatter
when building the shim `SKILL.md` for Claude.

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

## Skill Read Tracing

Because providers typically inject `SKILL.md` into the system prompt, there is
no native way to observe *which* skills were actually consulted in a session.
We work around this by splitting every deployed skill into a **thin shim** +
**companion content file**.

### Shim + Companion Pattern

- `SKILL.md` — a short shim with only frontmatter and an emphatic instruction to `Read` `content.md` first.
- `content.md` — the real skill body (exemplars, Disciplines, Anti-Patterns, etc.).

When the agent follows the instruction, it issues a `Read` tool call on
`<provider skills dir>/<id>/content.md` (Claude default:
`~/.claude/skills/<id>/content.md`). Tool calls are observable via
`PostToolUse` hooks.

### Deployment

`lib/skills/skill_deployer.py` and `lib/skills/skill_sync.py` automatically create this pair for:
- **Pattern skills** (`deploy_pattern_as_skill`)
- **Auto skills** (`deploy_rules_index_skill`)
- **Standalone skills** remain as full `SKILL.md` in source (no auto-shim) so drift detection stays accurate

### Trace Hook

`hook_manager.handlers.skill_read` runs on `PostToolUse` with matcher `Read`. It:
1. Checks if the read target is `<provider>/skills/*/content.md`
2. Parses `skill_id` from the path
3. POSTs an event to `POST /api/skill-reads` (stored in the `skill_reads` table)

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
