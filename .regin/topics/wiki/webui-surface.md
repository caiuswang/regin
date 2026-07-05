# WebUI surface map

regin's dashboard is a **Vue 3 single-page app** in `frontend/src/`, built with Vite to `web/static/dist/` and served by Flask. This topic is the **index of every page** — use it to find which view owns a route, then jump to the feature topic that explains that view's internals.

## How the SPA is built & served

- **Shell:** `_install_spa_routes` in `web/app.py:315-326` registers a catch-all (`/` and `/<path:path>`): any `api/*` path 404s, an existing file under `web/static/dist/` is streamed back, and everything else falls through to `index.html` so vue-router owns client-side routing.
- **Auth gate:** the `_enforce_auth` before-request hook (`web/app.py:254-268`) lets the SPA shell and `/static/` assets through unauthenticated (anything not under `/api/`), and 401s an `/api/*` call whose blueprint isn't in `PUBLIC_API_ENDPOINTS` when `get_current_user()` is null. A local recall-loopback bypass warms the dense models for the auto-inject hook.
- **API:** 21 Flask blueprints are registered in `web/app.py:74-155` (`auth_bp`, `hooks_bp`, `rules_bp`, `rule_engines_bp`, `repos_bp`, `meta_bp`, `plans_bp`, `patterns_bp`, `skills_bp`, `tags_bp`, `experiments_bp`, `trace_bp`, `topics_bp`, `prompt_templates_bp`, `settings_bp`, `schema_drift_bp`, `diagnostics_bp`, `memory_bp`, `grades_bp`, `grader_config_bp`, `events_bp`). Each view fetches its own `/api/…` slice from the matching blueprint via the shared `frontend/src/api.js` client.
- **Entry:** `frontend/src/App.vue` → `frontend/src/router.js` (route table) → `AppLayout.vue` (chrome: floating sidebar, command palette, theme toggle, mobile drawer).

## Routing & auth guard

Routes live in `frontend/src/router.js:39-86`. A global `beforeEach` guard (`router.js:95-105`):
- redirects to `/login` when there is no `regin_auth_token` in `localStorage` (every route except `meta.public`),
- additionally hides `/experiments/*` behind the `experimental_conceal` feature flag (`useFeatures` composable).

Several legacy paths redirect forward: `/rules/triggers` → `/trace/triggers`, `/skill-reads` → `/trace/skill-reads`, `/mcp-calls` → `/trace/mcp-calls`, `/tags` → `/patterns`, and `/tags/:name` → `/patterns?tag=…`.

## Sidebar nav groups

The sidebar is built from the `navGroups` computed in `AppLayout.vue:61-105`; the same array drives both the desktop floating sidebar and the mobile `<Drawer>`. Groups (and the flags that hide them):

| Group | Sidebar links (label → route) |
|---|---|
| **Library** | Repos `/repos` · Patterns `/patterns` · Skills `/skills` · Prompts `/prompt-templates` |
| **Observability** | Trace `/trace` · Live `/live` · Inbox `/inbox` *(unread badge)* · Memory `/memory` · Grades `/grades` · Audit `/audit` |
| **Engineering** | Rules `/rules` · Experiments `/experiments` *(flag: experimental_conceal)* · Plans `/plans` |
| **Diagnostics** *(hidden unless `diagEnabled`)* | Schema drift `/schema-drift` *(pending-drift badge)* · Payload log `/payload-log` |
| **System** | Settings `/settings` |

The sidebar shows only landing links; detail routes are reached by navigating in. Full route→view bindings in `router.js`: `/repos/:name` RepoDetailView, `/repos/:name/topics` RepoTopicsView, `/patterns/:slug` PatternDetailView, `/skills/:id` SkillDetailView, `/rules/:id` RuleDetailView, `/experiments/:id` ExperimentDetailView, `/plans/:filename` PlanDetailView.

**Not in the sidebar** (reachable by URL / menu): `/` DashboardView (landing) · `/login` LoginView (public) · `/account` UsersView (avatar menu) · `/ds` DesignSystemView (component gallery).

### `/trace` is a nested route

`TraceView.vue` is a thin shell that redirects `/trace` → `/trace/sessions` and hosts child routes (`router.js:63-77`): `sessions` **SessionsView**, `sessions/:id` **SessionTraceView**, `triggers` TriggersView, `triggers/raw` TriggersRawView, `skill-reads` SkillReadsView, `mcp-calls` MCPCallsView, `ingest-errors` IngestErrorsView.

> Reach a session by `/trace/sessions/<id>`. Deep detail on both views below lives in the **session-trace-design** topic.

## The two anchor views

These are the largest views on the surface and the richest examples of the composable-heavy pattern the frontend uses.

### SessionsView (`/trace/sessions`)

`frontend/src/views/SessionsView.vue` is the keyset-paginated session list. It drives a cursor feed through the `useCursor` composable against `/api/sessions` (page size 50; load-more appends, any filter change resets). The toolbar is a sticky two-tier filter bar (`useStickyHeader` measures its height into a CSS var):
- a search row — free-text `q` with a scope selector (Title / Prompt / Both) plus a trace-id prefix box;
- a faceted row of labelled pill dropdowns: **Range** (Today default; local-clock boundaries serialised as naive local ISO), **Kind** (real / test / all — seeded from the legacy `regin_sessions_show_tests` flag), **Workflow runs** (hide / show / only — an orthogonal `origin` axis), **Status** (active / inactive), and **Repo** (options from `/api/repos`).

Every facet persists to `localStorage`. Rows render through `SessionRow.vue` in a desktop `<table>` and a parallel mobile `<ul>` card list; each row carries an agent-kind glyph (`claude` / `codex` / `kimi` / generic / `workflow`), an active/ended/closed/test/workflow status pill, and a context-percentage badge. The view also owns checkbox multi-select → batch delete (`/api/sessions/batch-delete`), single delete, and a manual **Close** action (`/api/sessions/:id/close`) for a corrupt session that never emitted `SessionEnd`. Active-session detection is shared through `utils/sessionActivity.js`.

### SessionTraceView (`/trace/sessions/:id`)

`frontend/src/views/SessionTraceView.vue` is the single-session inspector — a live dashboard assembled almost entirely from composables (`useTraceData`, `useTraceScroll`, `useTraceTimeline`, `useTurns`, `useToolRollup`, `useWorkflowMeta`, `useSpanContentCache`, `useViewMode`, `useRuleTriggers`, `useCompactWatch`). Four view modes selected via `useViewMode` (`?view=` > localStorage > default): **conversation** (centered feed, `SessionConversationView`), **timeline** (PrimeVue TreeTable, `SessionTimelineTree`), **terminal** (flat log, `SessionTerminalLog`), and **messages** (the `send_to_user` feed with the session goal, fetched from `/api/sessions/:id/agent-messages`).

The header (`SessionTraceHeader` + `ToolTokenRollup` + `TraceOverviewStrip`) is sticky-pinned; span content loads on demand (`useSpanContentCache` overlays a per-span cache onto `session.spans` as `allSpans`); an opt-in right rail shows `SpanDetailPanel` + `SessionTurnsSidebar` with bidirectional turn⇄span highlighting. A **self-terminating live poll** (`LIVE_POLL_MS = 4000`, `startLivePoll` / `maybeStopOnConverge`) reconciles the tail to the DB while the session is live, then stops once `ended_at` is set and the newest id stops advancing; an already-closed session runs one bounded catch-up (`syncClosedSessionTail`) instead. Scroll/wheel pull-to-refresh (bottom) and pull-older (top) are wired by `useTraceScroll`, gated by `liveSyncActive`.

### LiveSessionView (`/live/:id?`)

`frontend/src/views/LiveSessionView.vue` is a mobile-first single-card session tail: one card with a header, a fold row, a scrolling tail (the only scroll region), and a sticky NOW zone, with row-detail / full-message / filter interactions in bottom sheets. Data + poll lifecycle live in `useLiveTail`; row semantics in `utils/liveRows.js`.

## Where to go deeper

This map is intentionally shallow. The biggest views have their own topics:
- **Sessions / SessionTrace** → `session-trace-design` (+ `trace-merge-reconcile`, `trace-span-capture`, `trace-usage-billing`)
- **Memory** → `memory-curation-surfaces` (+ the memory-recall / consolidation / engagement topics)
- **Inbox / Messages tab** → `agent-messages-inbox`
- **RepoTopics** → `topic-routing`, `topic-proposal-pipeline`, `proposal-review-comments`
- **Rules** → `rule-engine-design`
- **Experiments** → `pattern-experiments`
- **Dev-loop / styling gotchas** → `webui-dev-loop`, `webui-styling`

## gitnexus grounding (and its gap)

gitnexus's regin index models **Python call-flows**, not vue-router routes or Flask blueprint endpoints. A `query` for "session trace view page frontend session list" returned **0 processes** — only **File** nodes, surfacing `frontend/tests/session-trace.spec.js`, `frontend/tests/rule-trigger-deep-link.spec.js` and `lib/trace/trace_service/__init__.py` as the concept's central nodes, which confirms the session-trace surface as this topic's anchor but offers no route→view→handler edges. The index is also flagged **179 commits behind HEAD**. All route→view→blueprint mappings above are therefore grounded in the source files cited (`router.js`, `AppLayout.vue`, `web/app.py`, the view SFCs), not in the graph.