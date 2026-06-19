# WebUI surface map

regin's dashboard is a **Vue 3 single-page app** in `frontend/src/`, built with Vite to `web/static/dist/` and served by Flask. This topic is the **index of every page** — use it to find which view owns a route, then jump to the feature topic that explains that view's internals.

## How the SPA is built & served

- **Shell:** `web/app.py:313-322` resolves `web/static/dist/`, returns a real asset if the path exists, else falls back to `index.html` (client-side routing). The shell + `/static/` assets are public; everything else is auth-gated (`web/app.py:255`).
- **API:** 23 Flask blueprints are registered in `web/app.py:75-151` (`auth_bp`, `repos_bp`, `patterns_bp`, `skills_bp`, `trace_bp`, `topics_bp`, `memory_bp`, `grades_bp`, `settings_bp`, `schema_drift_bp`, `diagnostics_bp`, …). Each view fetches its own `/api/…` slice from the matching blueprint.
- **Entry:** `frontend/src/App.vue` → `frontend/src/router.js` (route table) → `AppLayout.vue` (chrome: sidebar, command palette, theme toggle).

## Routing & auth guard

Routes live in `frontend/src/router.js:38-84`. A global `beforeEach` guard (`router.js:93-103`):
- redirects to `/login` when there is no `regin_auth_token` in `localStorage` (every route except `meta.public`),
- additionally hides `/experiments/*` behind the `experimental_conceal` feature flag.

## Sidebar nav groups

The sidebar is built from `navGroups` in `AppLayout.vue:61-104`. Groups (and the flags that hide them):

| Group | Pages (route → view) |
|---|---|
| **Library** | `/repos` ReposView · `/repos/:name` RepoDetailView · `/repos/:name/topics` RepoTopicsView · `/patterns` PatternsView · `/patterns/:slug` PatternDetailView · `/skills` SkillsView · `/skills/:id` SkillDetailView · `/prompt-templates` PromptTemplatesView |
| **Observability** | `/trace` TraceView (shell) · `/inbox` InboxView · `/memory` MemoryView · `/grades` GradesView · `/audit` AuditView |
| **Engineering** | `/rules` RulesView · `/rules/:id` RuleDetailView · `/experiments` ExperimentsView *(flag: experimental_conceal)* · `/experiments/:id` ExperimentDetailView · `/plans` PlansView · `/plans/:filename` PlanDetailView |
| **Diagnostics** *(hidden unless diagEnabled)* | `/schema-drift` SchemaDriftView · `/payload-log` PayloadLogView |
| **System** | `/settings` SettingsView |

**Not in the sidebar** (reachable by URL / menu): `/` DashboardView (landing, stats from `meta.py`) · `/login` LoginView (public) · `/account` UsersView (avatar menu) · `/ds` DesignSystemView (component gallery).

### `/trace` is a nested route

`TraceView.vue` is a thin shell with child routes (`router.js:61-75`): `sessions` SessionsView, `sessions/:id` SessionTraceView, `triggers` TriggersView, `triggers/raw` TriggersRawView, `skill-reads` SkillReadsView, `mcp-calls` MCPCallsView, `ingest-errors` IngestErrorsView. Legacy top-level paths (`/skill-reads`, `/mcp-calls`, `/rules/triggers`) redirect into `/trace/*`.

> Reach a session by `/trace/sessions/<id>` — the bare `/sessions/<id>` silently renders blank (see the session-trace topic).

## Where to go deeper

This map is intentionally shallow. The biggest views have their own topics:
- **Sessions / SessionTrace** → `session-trace-design`
- **Memory** → `memory-curation-surfaces` (+ the memory-recall / consolidation / engagement topics)
- **RepoTopics** → `topic-routing`, `topic-proposal-pipeline`, `proposal-review-comments`
- **Rules** → `rule-engine-design`
- **Experiments** → `pattern-experiments`

## gitnexus grounding (and its gap)

gitnexus's regin index models **Python call-flows**, not vue-router routes or Flask blueprint endpoints: `route_map(repo=regin)` returned **0 routes**, and a `query` for "Vue SPA frontend views" returned only **File** nodes (no processes). The index is also flagged **15 commits behind HEAD**. The usable signal: that same query surfaced `frontend/src/router.js`, `AppLayout`, `TraceView.vue`, `SessionTraceView.vue` and `web/app.py` as the central File nodes for the concept — confirming them as this topic's anchors. All route→view→blueprint edges above are grounded in the source files cited, not in the graph.