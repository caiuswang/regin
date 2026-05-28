# Changelog

All notable changes to regin will be documented here. This project follows
[Semantic Versioning](https://semver.org/). While on `0.x`, breaking changes
may land in any minor release.

## [0.1.0] — 2026-05-28

First tagged release. regin is still **early beta**: database schemas, settings
keys, hook contracts, skill bundles, and CLI flags may change without
backward-compatible shims.

### Highlights

- **Pattern & skill management** — author local patterns, promote them to
  versioned skill bundles, deploy to the active provider's skills directory.
- **Rule engines** — pluggable lint/rewrite engines (GritQL, bundle, radon)
  wired into Claude `PreToolUse` / `PostToolUse` hooks to enforce conventions
  in flight, not just document them.
- **Session tracing** — web UI ingests Claude hook events into a searchable,
  filterable, replayable session viewer with token/skill/advisor rollups.
- **Topic wikis** — per-repo knowledge stores with embedding-based on-demand
  retrieval, so the agent pulls in only the slices relevant to its current
  task.
- **Web dashboard** — Vue 3 SPA on `:8321` for patterns, rules, traces, repos,
  topics, and user/auth management.

### Known limitations

- **Claude-only.** Codex and other providers are scaffolded as stubs but not
  wired through the rule layer.
- **Schema drift.** `regin init` builds the DB from `db/schema.sql`; any new
  Alembic migration must also be folded into that file or fresh installs will
  diverge.
- **No PyPI package.** Run from a checkout via `.venv/bin/python cli/regin.py`.
- **Skill publishing not wired up.** `pattern promote` builds a versioned
  bundle locally but external publishing is not enabled in this release.
