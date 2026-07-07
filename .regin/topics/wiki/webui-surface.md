# WebUI surface map

regin's dashboard is a **Vue 3 single-page app** in `frontend/src/`, built with Vite to `web/static/dist/` and served by Flask. This topic is the **index of every page** — use it to find which view owns a route, then jump to the feature topic that explains that view's internals.

## How the SPA is built & served

- **Shell:** `_install_spa_routes` in `web/app.py:329` registers a catch-all (`/` and `/<path:path>`): any `api/*` path 404s, an existing file under `web/static/dist/` is streamed back, and everything else falls through to `index.html` so vue-router owns client-side routing.
- **Auth gate:** the `_enforce_auth` before-request hook (`web/app.py:269`, installed by `_install_auth_gate`) lets non-`/api/` paths through unauthenticated (the SPA shell and `/static/` assets), waves through app-level routes and any endpoint in `PUBLIC_API_ENDPOINTS`, and 401s every other `/api/*` call when `get_current_user()` is null. A local recall-loopback bypass (`_inject_recall_loopback_ok`) warms the dense models for the auto-inject hook.
- **API:** 22 Flask blueprints are registered in `web/app.py:79-163` (`auth_bp`, `hooks_bp`, `rules_bp`, `rule_engines_bp`, `repos_bp`, `meta_bp`, `plans_bp`, `patterns_bp`, `skills_bp`, `tags_bp`, `experiments_bp`, `trace_bp`, `topics_bp`, `prompt_templates_bp`, `settings_bp`, `schema_drift_bp`, `diagnostics_bp`, `memory_bp`, `grades_bp`, `grader_config_bp`, `events_bp`, `bridge_bp`). Each view fetches its own `/api/…` slice from the matching blueprint via the shared `frontend/src/api.js` client.
- **Entry:** `frontend/src/App.vue` → `frontend/src/router.js` (route table) → `AppLayout.vue` (chrome: floating sidebar, command palette, theme toggle, mobile drawer).

## Routing & auth guard

Routes live in `frontend/src/router.js:40-85`. A global `beforeEach` guard (`router.js:96-108`):
- redirects to `/login` when there is no `regin_auth_token` in `localStorage` (every route except `meta.public`),
- additionally hides `/experiments/*` behind the `experimental_conceal` feature flag (`useFeatures` composable).

Several legacy paths redirect forward: `/rules/triggers` → `/trace/triggers`, `/skill-reads` → `/trace/skill-reads`, `/mcp-calls` → `/trace/mcp-calls`, `/tags` → `/patterns`, and `/tags/:name` → `/patterns?tag=…`.

## Sidebar nav groups

The sidebar is built from the `navGroups` computed in `AppLayout.vue:61`; the same array drives both the desktop floating sidebar and the mobile `<Drawer>`. Groups (and the flags that hide them):

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

`TraceView.vue` is a thin shell that redirects `/trace` → `/trace/sessions` and hosts child routes (`router.js:68-76`): `sessions` **SessionsView**, `sessions/:id` **SessionTraceView**, `triggers` TriggersView, `triggers/raw` TriggersRawView, `skill-reads` SkillReadsView, `mcp-calls` MCPCallsView, `ingest-errors` IngestErrorsView.

## Anchor views (pointers, not internals)

The three richest views on the surface each have their own topic; this map only places them on their routes and hands off.

- **SessionsView** (`/trace/sessions`) — the keyset-paginated session list with a sticky faceted filter bar (range / kind / workflow-origin / status / repo), per-row agent glyph and status pill, batch-delete and manual **Close**. Fed by `/api/sessions` via the `useCursor` composable. Internals in `[[session-trace-design]]`.
- **SessionTraceView** (`/trace/sessions/:id`) — the single-session inspector with conversation / timeline / terminal / messages view modes and a self-terminating live poll, fed by `/api/sessions/:id/map`. Internals in `[[session-trace-design]]` (+ `[[trace-merge-reconcile]]`, `[[trace-usage-billing]]`).
- **LiveSessionView** (`/live/:id?`) — a mobile-first single-card session tail (header, fold row, scrolling tail, sticky NOW zone; bottom-sheet interactions via `LiveSheet`), driven by the `useLiveTail` composable and `utils/liveRows.js`. Internals in `[[live-session-mobile-card]]`.

## Where to go deeper

This map is intentionally shallow. The feature views have their own topics:
- **Sessions / SessionTrace** → `[[session-trace-design]]` (+ `[[trace-merge-reconcile]]`, `[[trace-span-capture]]`, `[[trace-usage-billing]]`)
- **Live phone card** → `[[live-session-mobile-card]]`
- **Memory** → `[[memory-curation-surfaces]]`
- **Inbox / Messages tab** → `[[agent-messages-inbox]]`
- **RepoTopics** → `[[topic-routing]]`, `[[topic-proposal-pipeline]]`, `[[proposal-review-comments]]`
- **Rules** → `[[rule-engine-design]]`
- **Experiments** → `[[pattern-experiments]]`
- **Dev-loop / styling gotchas** → `[[webui-dev-loop]]`, `[[webui-styling]]`

## gitnexus grounding (and its gap)

gitnexus's regin index models **Python call-flows and API routes**, not vue-router routes or Vue components. A `route_map` for `/api/sessions` surfaces the handler edges that back these views: `/api/sessions`, `/api/sessions/<trace_id>/map`, `/api/sessions/<trace_id>/close`, and `/api/sessions/batch-delete` all resolve to `web/blueprints/trace/sessions.py` (flow `Api_sessions → _extract_key / Encode_cursor`, the keyset cursor), `/api/sessions/<id>/agent-messages` to `web/blueprints/trace/agent_messages.py`, and the `/live` card's `bridge-*` routes to `web/blueprints/bridge.py`. The graph exposes **no** route→view→handler edge (Vue SFCs aren't graph nodes), so every route→view binding above is grounded in the source files cited (`router.js`, `AppLayout.vue`, `web/app.py`, the view SFCs), not the index — which is also 2 commits behind HEAD.