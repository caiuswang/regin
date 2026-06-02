# Changelog

All notable changes to regin will be documented here. This project follows
[Semantic Versioning](https://semver.org/). While on `0.x`, breaking changes
may land in any minor release.

## [0.2.0] — 2026-06-02

Still **early beta** — the `0.1.0` caveats below all stand (schemas, settings
keys, hook contracts, and CLI flags may still shift).

### Headline: workflow-run capture

The marquee feature of this release. Claude Code dynamic-workflow runs are now
captured and rendered in the trace UI as first-class entities — the same
visibility regin already gave ordinary sessions, extended to multi-agent
orchestration:

- Each run is linked to its parent session and split onto its own
  `sessions.origin` axis (separate from ordinary sessions; `agent_type` is now
  vendor-only).
- A **phase rail** renders live from the run manifest — per-agent model,
  tokens, and phase/state — with agents ordered prompt→work→result and subagent
  spans railed under their phase. Declared phases show before they run; stale
  snapshots are flagged in the header.
- Per-run token accounting: a total-tokens chip in the header, agent tool calls
  counted from captured spans (not the manifest), and a fix for the bogus
  ~111.5k "untagged" split.

### Added

- **Token-cost accounting.** Per-span and per-turn `cost_usd` priced from
  models.dev, including context-tier (>200K) pricing and routing-suffix key
  matching. New `regin trace backfill-costs` fills NULL costs and re-prices
  existing `turn_usage` / session totals.
- **Conversation pins & follow-tail.** Pin any span to hold its on-screen
  position across the live poll, or follow-tail to stick to the newest activity
  like a terminal (with a "N new" hint while scrolled up).
- **Trace header insights.** Cache read/write surfaced; `+sub` peak shown as
  absolute tokens.
- **Actionable rule warnings.** Rule-engine output now carries each rule's
  `detail` (what tripped, e.g. `aggregate CC=180`) to the agent and trace UI.
- **vue-complexity rule engine** for `.vue` SFCs, plus a `regin trace`
  capture-models doc.

### Changed

- **Live rescan** capture refactor; `SessionTraceView` split into composables.
- **Context windows** honor the configured size for `[1m]`-suffixed models;
  a known model that overflows its window holds it (UI shows >100%) rather than
  inflating the denominator.
- **Queued prompts** reconstructed by replaying queue-ops (`enqueue` /
  `dequeue` / `popAll`) in arrival order instead of counting removes.

### Fixed

- Real-transcript usage tests reconciled with the window-cap contract.

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
