# Changelog

All notable changes to regin will be documented here. This project follows
[Semantic Versioning](https://semver.org/). While on `0.x`, breaking changes
may land in any minor release.

## [0.4.0] — 2026-10-23

Still **early beta**. This release removes the pre-v0.3 compatibility code, so
an install older than v0.3.0 must be brought to v0.3.0 and migrated first.

### Headline: the feature pipeline

regin grows from an observability tool into a workbench: a feature moves
spec → tasks → done-claim → defects, every task is launched and steered as an
agent run, and every defect is recorded against the feature that shipped it.

### Added

- **Feature pipeline.** Features with a spec body, stages, images, branch,
  follows/related links and a ledger of events; per-project step stages with
  their own prompts; Overview, Features, Tasks (Board / List / lane Graph) and
  Defects tabs with live refresh; a pixel theme alongside the default.
- **Task runs.** Arm a task onto Claude (Agent SDK or tmux), Kimi (ACP or
  tmux) or CodeBuddy/WorkBuddy; runs survive a server restart (`launch=tmux`),
  file their own report on the task, honour typed dependency/order edges,
  carry an output language, and preview the launch prompt before arming.
- **Defect ledger.** Open, triage, attribute and close defects against a
  feature; one fix task for several defects; fix-drift flags; a run never
  settles a fix task done over an open defect.
- **@-references.** A reference grammar and `@` composer in every body editor;
  references render as live chips, list backlinks, and reach the agent as a
  `<references>` block in the run prompt.
- **Projects and workspaces.** Workspaces group repos into one pipeline scope
  (`?project=ws:<id>`); an add-project dialog at every entry point.
- **Providers.** CodeBuddy/WorkBuddy family — hooks, skills deploy, MCP
  registration, transcript import, ACP launch; Kimi Code reaches parity
  (workspace trust, resume over ACP `session/load`) without the experimental flag.
- **`regin` MCP server.** One server carries `send_to_user` and the pipeline's
  read/write surface (features, tasks, defects, images); `regin mcp register`.
- **Agent SDK tier and /live.** Launch, resume, cancel and queue runs from
  `/live`; approve permission and plan requests from the phone; held
  permission channel (`regin bridge hold/release`); Mod listener bridge with
  `regin-band`, `regin-ask`, `regin-permit`, `regin-meter`, `regin-compact`.
- **File viewer.** Read-only Monaco code view, rendered markdown/HTML/CSV/
  notebooks and native media under `/api/files`.
- **Trace.** Redesigned session-trace chrome, Timeline and conversation feed;
  background shells as their own block; `regin stats session` diagnoses why a
  session was slow; tiered notifications over SSE.
- **Install.** One-click native Windows install; re-entrant `setup.sh`;
  `regin uninstall` removes only what this checkout can prove it wrote.
- **Frontend tooling.** `scripts/dom-measure.mjs` (`--explain`, `--overflow`,
  `--baseline`), `Ctrl+Shift+G` element→source grab, design-system manifest.

### Changed

- **Breaking:** runtime DDL and pre-v0.3 compat code dropped; `regin migrate`
  refuses incomplete unstamped DBs; `regin rebuild` touches only cache tables.
- **Breaking:** `verify_cmd` retired; the `feature_key` string model and
  `regin task ship` replaced by the `features` table and `regin feature ship`.
- Sidebar regrouped into Workbench / Observability / Library / Engineering;
  Grades moved under Settings; Patterns and Skills share one entry.
- Large modules split (F123) and their worst functions brought under the
  radon threshold.

### Fixed

- 192 fixes since v0.3.0, including D317–D321: SDK task runs whose agent is
  gone settle at boot and on Stop; rewording a defect refreshes its fix task;
  the website fidelity pins carry the Windows quick-start additions.

## [0.3.0] — 2026-07-13

### Headline: self-evolving topic wikis, agentic memory, mobile remote control

### Added

- **Topic wikis & proposals.** Drift detection (rename-follow and content
  drift) with refresh proposals, topic→memory cascade, gated auto-spawn of the
  drafting agent (off by default); git-shareable proposal bundles and an MCP
  server for the proposal lifecycle; tree navigator, word-level wiki diffs,
  Apply All; wiki recall signal from the trace.
- **Agent memory.** `reflect` v4 single agentic "dream" stage with a Doctor
  page; topic-taxonomy tree navigation; `link-topics`; `<skill_experience>`
  injection; mandatory lesson titles; supersede; corpus export/import;
  `curate-apply`.
- **Trace enrichment.** `prompt_id` join, refusal marker, nested-subagent
  parentage, ScheduleWakeup resume links, tool-cost drill-down, encrypted
  thinking split out, cancelled/denied tool spans captured.
- **Mobile `/live` & agent bridge.** HTTP→live-Claude bridge over tmux, terminal
  peek, session-tail card, answering AskUserQuestion from the phone.
- **Sessions, prompts & inbox.** Session tags, composable prompt surfaces,
  notification event bus with webhook/Telegram/Lark push channels, inbox
  search and filters.
- **goal-verified loop.** Preflight roadmap router, span gates, fleet-wide
  `ui-verified` gate, regin-agnostic plugin skills.

### Changed

- New Reka-UI primitive set and design-system view; responsive SPA.
- Kimi Code provider parity and multi-provider settings.
- Schema migration driven from Alembic (boot self-heal removed).
- Radon and Vue complexity thresholds cleared across cli/lib/web.

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
