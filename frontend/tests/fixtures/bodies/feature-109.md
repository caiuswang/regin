# Feature 109 — Redesign the four Pipeline pages (design handoff)

## Intent
Feature #108 (f54b1341) made Tasks / Features / Defects / Projects share one *behaviour* chassis: the toolbar, the list envelope, `ListStatus`, the pager, and URL state. The *visual* layer was left as each tab grew it. The results:
- The same colour means four different things.
- The rows use three different grammars.
- The narrow layout is patched per component.

This spec is the brief for a design pass (ui-ux-pro-max → design system). It gives the facts a designer needs first: entities, lifecycles, fields, fixed constraints, and known defects. It then names the decisions the design must return, and the checks that decide whether the redesign landed.

Non-goals:
- Changing the #108 contract (the `ListPage` envelope, URL-as-state, the toolbar's control order, `ListStatus` states, invariants I1–I5).
- The detail pages (`/tasks/:id`, `/features/:id`, `/defects/:id`, `/projects/:id`).
- The task Graph canvas.
- The triage queue's key bindings.
- New API fields. If the design needs data the envelope does not carry, the design lists it as a follow-up; it does not assume it.
- Replacing regin's tokens with a generated palette or fonts.

## Pre-knowledge — the platform (fixed)
| Axis | Fact | Source |
|---|---|---|
| Stack | Vue 3 SFC + Tailwind v4 `@theme`. No component library. Primitives live in `components/ui/`: Button (primary/secondary/danger/ghost/link; sm h-7, md h-9), Tabs (segmented/underline), Select, Input, Checkbox, Popover, DropdownMenu, Dialog, Tooltip, Icon, ClampedText, CopyId. `Badge`, `Card`, `StatCard` and `KebabMenu` live in `components/` | `frontend/src/components/ui/` |
| Tokens | Semantic aliases over the slate ramp:<br>• `--color-background/surface/surface-2/-3/-selected`<br>• `fg/-muted/-subtle/-faint`<br>• `border/-subtle/-strong`<br>• `primary`<br>• `danger/success/warning/info` (+`-strong`, `-soft`)<br>• `agent` (violet)<br>• radius md .5rem / lg .625rem / xl .875rem<br>• `--ds-shadow-sm/md/lg`, z-scale<br>Spacing uses the Tailwind defaults. There is no custom sans font; mono is `--font-mono` | `frontend/src/assets/style.css:32-150`; manifest `frontend/design-system.json` (regenerate with `node scripts/gen-design-manifest.mjs`) |
| Theme | Dark mode is `html[data-theme=dark]`, which inverts the whole ramp. Every colour decision must hold in both themes | `useTheme.js`, `style.css:190+` |
| Icons | SVG only, through `ui/Icon.vue`. No emoji as icons | memory f5786adf |
| Shell | `PipelineView`, top to bottom:<br>1. eyebrow "Engineering · Pipeline" + h1 + subtitle, which the tab publishes<br>2. `#pipeline-actions` teleport slot, which holds the primary create button<br>3. project `Select` (`pipeline-project`), hidden on Projects<br>4. `nav.segmented` tab strip with count pills<br>5. the tab body<br>Tabs are kept alive (`CACHED_TABS`) | `views/PipelineView.vue:117-180` |
| Chassis | `PipelineListToolbar`, left to right: view switch · search (Enter commits, Esc reverts) · facet selects · `#controls` slot · sort key + direction · Clear N · count slot. Below it: `ListStatus` (loading · error · empty · no-match · past-end · rows), then `PageControls` (25/50/100/200) | `components/PipelineListToolbar.vue`, `ListStatus.vue`, `PageControls.vue` |

### ui-ux-pro-max output — kept vs rejected
`search.py "developer tool dashboard data-dense list table issue tracker pipeline" --design-system`
| Recommendation | Verdict | Why |
|---|---|---|
| Style: Data-Dense Dashboard (minimal padding, row hover highlight, filters) | **keep** | These are operator work-queues, not a marketing surface |
| Pattern: Comparison Table + CTA (hero, pricing) | reject | A landing-page pattern; there is no hero on a work list |
| Palette #1E40AF / #F59E0B CTA / #F8FAFC | reject | Would fork the token set. Map intent onto the existing `primary`/`warning`/`surface` aliases |
| Fira Code + Fira Sans | reject | Adds a web-font dependency for no job the system stack fails at. Mono stays for ids and numbers only |
| Checklist: 4.5:1 contrast, visible focus, reduced motion, 150–300 ms transitions, 375/768/1024/1440 | **keep** | These become the acceptance items below |

## Pre-knowledge — the four entities
```ts
type Task = { id; title; status: TaskStatus; priority: 'urgent'|'high'|'medium'|'low'|'none';
  kind; feature_id?; feature_title?; project?; tags: string[]; images: number;
  attempts: number; live: boolean; created_at; updated_at }
type Feature = { id; title; external_ref?; stage: Stage; rounds: number;
  sessions: number; tasks: number; cost: number|null /* null = "none declared", never $0 */;
  open_defects: number; defects: number; escapes: number; last_activity }
type Defect = { id; symptom; status: 'open'|'closed'; round: number; triage_state;
  feature_id?: number /* null = unattributed */; images; suspect_paths; missing_check;
  declined_because; opened_at }            // there is NO severity field
type Project = { id; name; description?; status: ProjectStatus;
  progress: { shipped; escaped; building; spec } /* abandoned is off the bar */;
  features: number; open_tasks: number; open_defects: number; activity }
```

### Lifecycles
Task (`TaskStatus`)
```
idea → armed → queued → running → done
                           └────→ failed → (re-arm) armed
any ──archive──→ archived ──restore──→ prior
```
Feature (`Stage`)
```
spec → building → shipped ──defect escapes──→ escaped → building (a new round)
any ──abandon──→ abandoned   (a decision, not a failure; costs no round)
```
Defect status (axis 1) and triage state (axis 2) are orthogonal. Draw them apart.
```
status:  open ⇄ closed
triage:  untriaged → accepted | duplicate | declined | snoozed
```
Project status is derived server-side (`lib/pipeline/projects.py::status_of`). It is not a lifecycle a user walks:
`empty("Backlog") | planned | in_progress | completed`.

## Pre-knowledge — current visual defects (what the design must resolve)
| # | Defect | Evidence |
|---|---|---|
| V1 | Amber carries four meanings: queued task, high priority, open defect, escaped feature. The eye cannot tell "needs attention" from "in flight" | `TaskStatusIcon.vue:80`, `taskMeta.js:16`, `DefectRow` `ledger-tag-amber`, `ledgerFormat.js:10` |
| V2 | Blue carries three meanings: armed task (`info`), building feature, in-progress project. Purple carries two: a planned project and task tags. The `agent` violet (running) sits next to both | `projectFormat.js:14`, `ledgerFormat.js:10` |
| V3 | Four status grammars: task status is an icon with no text, feature stage and project status are `Badge` chips, and defect status is a `ledger-tag` | as above |
| V4 | Three row grammars: Tasks uses 1-line rows with hover-revealed actions, Features and Projects use `.tbl` tables (Projects inside a Card, Features not), and Defects uses `.card` list items with a Details disclosure | `TaskRow.vue`, `FeatureIndexTable.vue`, `ProjectsView.vue`, `DefectRow.vue` |
| V5 | Narrow layout is patched per component:<br>• TaskRow hides facts and date at 40rem<br>• DefectRow hides the date at 639px<br>• FeatureIndexTable turns into a sideways scroller at 768px<br>• Projects uses an `overflow-x-auto` wrapper<br>No shared breakpoint | `TaskRow.vue:321-337`, `DefectRow.vue:164`, `FeatureIndexTable.vue:218` |
| V6 | Summary widgets have no shared form: the Features coverage strip is a text line, and Projects has none on the list (StatCards only on the panel) | `FeaturesView.vue:163-180`, `ProjectPanelView.vue` |
| V7 | Hover-revealed task actions are invisible to keyboard and touch until focus lands (`hover:none` shows them always) | `TaskRow.vue:321` |

## Design decisions the pass must return
| # | Decision | Constraint |
|---|---|---|
| Q1 | **Semantic status scale.** One mapping from every value in the four lifecycles to a tone. The tones are:<br>• neutral / not started<br>• in flight<br>• attention (a human must act)<br>• done-good<br>• done-bad<br>• decided-off (abandoned, archived, declined) | Uses only existing tokens (add a token only if no alias fits, and name it). No tone carries two meanings (fixes V1, V2). The agent violet is reserved for "an agent is running now" |
| Q2 | **One status mark.** A single component (icon + label, or dot + label) used by all four tabs | Colour is never the only carrier: a text label or a distinct glyph always accompanies it (WCAG 1.4.1) |
| Q3 | **Row grammar per tab.** Choose list-row or table for each tab, and say why | Rows are ≤ 2 lines at rest (#108 D11). The id is mono and the title is the primary text. The whole row is a link target, except controls inside it. Actions are reachable without hover |
| Q4 | **Density.** Row height, cell padding, font sizes, and one comfortable/compact decision | Tailwind spacing scale. Body text contrast ≥ 4.5:1 in both themes |
| Q5 | **Responsive.** One shared breakpoint set, and per tab which columns/facts collapse at each width | Covers 375 / 768 / 1024 / 1440. No sideways page scroll at 375 |
| Q6 | **Tab summary band.** Whether each tab gets a one-line summary above the list, and what it holds | Features' coverage line must keep its semantics (attributed sessions, cost vs "none declared", link to unattributed defects). Built only from `population` / `extras` that already exist |
| Q7 | **Defect row.** How the expanded body and actions look (Close/Reopen + ⋯ Attribute / Create fix task / Link task / Merge) | Actions are keyboard-reachable. The triage queue is restyled only: same bindings, same order |
| Q8 | **Shell.** How these sit together: eyebrow, h1, subtitle, action slot, project scope, tab strip with counts | The project select stays the only `?project=` writer (#108 I4). The count pills stay the population numbers (I1) |

## Handoff artefacts the design pass produces
| Artefact | Where |
|---|---|
| Tone mapping (Q1) as a decision table | rewritten into this spec's Q1 row |
| Any new or changed token | `frontend/src/assets/style.css` `@theme` + regenerated `frontend/design-system.json` |
| A per-tab layout (row anatomy, columns per breakpoint, summary band) | this spec, replacing Q3–Q6 with the chosen answer |
| A mock for each tab × {light, dark} × {1440, 375} × {rows, empty, no-match} | attached to the feature, not committed to `docs/` |

## Rules
| When | Then |
|---|---|
| A value is rendered anywhere on the four tabs | its tone comes from the Q1 table through one shared helper, not a per-file map (`STAGE_COLOR`, `STATUS_COLOR`, `.ts-*`, `ledger-tag-*` fold into it) |
| A state is "no data" (feature cost null, stage unknown) | it wears no tone chip; it shows muted text ("none declared" / nothing). A missing value must not look declared |
| A count is 0 | it renders muted, not in the attention tone (Projects' amber open-defects applies only when > 0) |
| A row is quiet (a project with no activity) | de-emphasised by a token (`fg-subtle`), not by opacity on the whole row, so contrast stays ≥ 4.5:1 |
| A control is hover-revealed | it is also shown on `:focus-within` and under `hover:none` |
| Motion (row hover, disclosure, dimmed reload) | 150–300 ms, disabled under `prefers-reduced-motion` |

## Invariants
- D1: across the four tabs, each tone means exactly one thing (one row per tone in Q1).
- D2: every status on the four tabs renders through the one status-mark component.
- D3: no `.vue` under the four tabs or their row components holds a literal colour or a status→colour map.
- D4: the #108 invariants I1–I5 still hold.

## Acceptance
```gherkin
Scenario: One tone, one meaning
  Given the Q1 tone table
  Then no tone name appears against two semantic categories
  And grep for STAGE_COLOR|STATUS_COLOR|ts-queued|ledger-tag-amber in frontend/src finds only the shared helper

Scenario: Status is never colour-only
  Given each of /tasks, /features, /defects, /projects with rows
  Then every [data-testid=status-mark] has non-empty accessible text

Scenario Outline: Contrast holds in both themes
  Given <route> in data-theme=<theme>
  Then an axe-core run reports 0 color-contrast violations
  Examples: route ∈ {/tasks,/features,/defects,/projects} × theme ∈ {light,dark}

Scenario Outline: No sideways scroll on a phone
  Given <route> at 375×812
  Then `node scripts/dom-measure.mjs --route <route> --overflow` reports no horizontal overflow of .content-scroll
  Examples: /tasks, /features, /defects, /projects

Scenario: Row actions without hover
  Given /tasks with rows
  When I Tab onto a task row
  Then its run/edit/archive actions are visible and focusable

Scenario: Missing is not zero
  Given a feature with cost = null
  Then its cost cell reads "none declared" and wears no tone chip

Scenario: #108 contract untouched
  Then frontend/e2e specs covering the pipeline chassis pass unchanged (toolbar under tabs, Clear N, count = tab pill, ListStatus states)
  And responsive.spec.js passes
```
