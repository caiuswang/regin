# Feature #110 — Pipeline page redesign

## Intent
Rebuild the `/pipeline` shell and its tabs to match the claude.ai/design prototype
`Pipeline Redesign.dc.html` (project `07e61ff2-b356-461d-982e-0b64e283798c`, read via DesignSync `get_file`; re-fetch before building — the file is still being extended; last re-fetched 2026-09-28 (sha1 44dd8b97): since the 2026-09-27 fetch the palette went quiet — tinted highlights became neutral surfaces with coloured text — and 1c's rows gained a segmented stage bar and an "Active tasks" section; `screenshots/1c-fold.jpg` shows the fold on the *earlier* row design, so for rows the HTML wins).
Targets: desktop **1a, 1d, 1b, 1g, 1h, 1e, 1f, 1c** and mobile **2a, 2b, 2c, 2d**. **0a** is today's baseline, not a target.
Operator rule: **copy every design in the prototype; do not declare done until each is present. Anything the prototype does not draw is asked, not invented.**

**Overview override (1a, 1d, 2a).** The operator judged the built Overview too busy — too many elements, too much colour — and it departs from the prototype on purpose. Measured on the live page (2026-09-28, 1440 px, light): 225 visible nodes, 8 panels, 6 hue families, 19 dot markers, 5 caption sentences. Blue meant Armed, Building and link at once; orange meant Queued and Triage. The Overview follows option **A "Quiet loop"** (operator-approved) in `docs/ui-sketches/pipeline-overview-declutter/`. Renders: `a-quiet-loop-{light,dark,phone}.png`, source `a-quiet-loop.html`, today's baseline `0-current-light.png`. The same sketch measures 115 nodes, 4 hue families and 0 dots. Where this spec and the prototype disagree on the Overview, this spec wins. The rest of the prototype still binds.

| Option | Idea | Verdict |
|---|---|---|
| **A Quiet loop** | same panels and API; loop → one card; only the counts that ask for action are shown or coloured; Escaped panel and Projects chrome removed | **chosen — operator approved 2026-09-28**; smallest change that removes most of the noise |
| B One queue (`b-one-queue-light.png`) | loop → one KPI line; Running, Run next, decisions and failed merge into one list grouped by verb (Watch · Decide · Run · Retry) | rejected — needs new ordering rules and a Retry group no endpoint serves, and it loses the distribution bars |
| C Now \| Health (`c-now-health-light.png`) | left: one action card; right: loop stacked vertically, then projects | rejected — breaks the loop's left-to-right Tasks → Features → Defects reading |

The design's thesis:
1. header collapses to **one line** (title · project chip · updated-at · create button);
2. **three tabs only — Overview · Tasks · Features.** Defects and Projects stop being tabs: defects are reached through the Overview "Needs a decision" panel, the Features triage rail and expanded feature rows; projects through the project chip/picker and the Overview Projects panel;
3. every toolbar becomes **search + removable filter chips + one "Filter" button** (replaces 8 selects/toggles);
4. row actions are **never hover-only** — each row shows the ONE next action it needs (Run / Arm / Watch / Attribute).

Non-goals: the sidebar's desktop layout (only its Pipeline link target changes); the detail pages `/features/:id`, `/defects/:id`, `/tasks/:id` beyond staying inside the shell; the prototype's "Try next" ideas (board swimlanes, stage rail on Defects).

## Surface map

| Option | Route | Replaces | Data source (existing unless NEW) |
|---|---|---|---|
| shell | `/pipeline` layout, redirect → `/overview` | `PipelineView.vue` header, `Select` scope, 4-tab strip | `/api/pipeline/counts` |
| 1a Overview, all projects | `/overview` NEW — drawn as the Quiet loop (Overview override) | — | NEW `GET /api/pipeline/overview?project=` |
| 1d Overview, one project + picker | `/overview?project=<id>`; picker on the chip | `ProjectPanelView`, `ProjectsView`, scope `Select` | same endpoint; `/api/pipeline/projects` + NEW `attention` sort |
| 1b Tasks · Board | `/tasks?view=board` | — (new view) | NEW `GET /api/tasks/board` (per-status columns; `column=<status>&page=` pages one) |
| 1g / 1h Board, little data | same route — the board's layout when columns are empty (1g: only Done has cards; 1h: some empty) | fixed 5-equal-column grid in `TaskBoard.vue` | `/api/tasks/board` + NEW top-level `done_week` |
| 1e Tasks · List (grouped by next step) | `/tasks?view=list` (default) | `TaskListPanel` / `TaskRow` | `/api/tasks` + NEW `group=next_step` (`only=<group>&page=` pages one group) |
| 1f Tasks · Graph | `/tasks?view=graph` | `TaskGraphPanel` (deleted) | NEW `GET /api/tasks/graph?lanes=1&open=0|1&project_id=` → nodes (+`feature_id`), edges (+`blocking`), `lanes[{feature_id,title,done,total}]`, `chains`, `lanes_hidden`; client only places boxes |
| 1c Features | `/features` | `FeatureIndexTable` + `DefectTriageQueue` (moved from Defects tab) | `/api/features` + NEW `extras.stage_counts` + `extras.attribution` + row `tasks_done`; `/api/defects` untriaged |
| 2a–2d mobile | same routes, < 768 px | responsive fallbacks | same |

### Retired tabs — deleted, no redirects

| Route / file | Fate |
|---|---|
| `/defects`, `/projects`, `/projects/:id` | route entries deleted; no redirect, no compatibility handling |
| `DefectsView`, `ProjectsView`, `ProjectPanelView`, `components/projects/*` | deleted |
| `/defects/:id` | kept — the new defect rows (Needs a decision, rail cards, expanded feature rows) link to it |
| `/defects?feature_id=` links (ledger defects tile, index defect count) | now `/features/:id#feature-defects` — the feature page's own defect list, revealed on arrival |

Sidebar Pipeline link → `/overview`; its `match` list becomes `['/overview','/tasks','/features','/defects']`.

## Shape

```ts
type Tab = 'overview' | 'tasks' | 'features'
type Scope = 'all' | 'none' | `${number}`                  // unchanged: lib/pipeline/counts.py::parse_scope
type Priority = 'urgent' | 'high' | 'medium' | 'low' | 'none'   // 4-bar glyph
type TaskStatus = 'idea' | 'armed' | 'queued' | 'running' | 'done' | 'failed'
type Stage = 'spec' | 'building' | 'shipped' | 'escaped'    // abandoned never shown: out of the default
                                                            // /api/features list, its population and the Features
                                                            // tab count; reachable only by `stage=abandoned`

interface ShellHeader {                        // ONE row; no eyebrow, no subtitle paragraph
  title: 'Pipeline'
  projectChip: { label: 'All projects' | 'No project' | string; hotkey: 'p' }
  updatedAt: string                            // "Updated just now" — last successful counts/overview read
  create: { label: 'New task' | 'New feature'; hotkey: 'c' }   // 'New feature' on Features, else 'New task'
  subline?: string                             // Features only: "61% of sessions attributed · $412 of $655"
  tabs: [{ tab: 'overview' }, { tab: 'tasks'; count: number }, { tab: 'features'; count: number }]
}

// 1d picker (dropdown off the chip)
interface ProjectPicker {
  search: { placeholder: `Find among ${number} projects` }
  allRow: { label: 'All projects'; hint: 'portfolio view' }
  groups: Array<{ label: 'Needs attention' | 'Recent'; items: PickerItem[] }>
}
interface PickerItem { name: string; selected: boolean /* ✓ + tinted */; stageBar: StageBar;
  openDefects: number /* "N open" warning colour, "—" when 0 */ }
type StageBar = { spec: number; building: number; shipped: number; escaped: number }

// 1a / 1d — NEW GET /api/pipeline/overview?project=<scope>
interface Overview {
  loop: [LoopStage, LoopStage, LoopStage]      // ONE card, three columns split by --color-border dividers; no arrows,
                                               //   no per-stage cards (phone: the same card, stages stacked)
  loopNote?: string                            // "↺ 4 fix tasks carry open defects back into the queue" — the loop card's
                                               //   footer row, --color-fg-subtle text, no fill, not danger
  running: { total: number; rows: RunningRow[] }        // "Running now" · n · "Open Live →" (/live)
  runNext: { total: number; ready: number; rows: RunNextRow[] }   // "Run next" · n · "All in Tasks →"
  needsDecision: TriageRow[]                             // header: "Needs a decision" · n (the Defects stage's Triage part) · "Triage all →" (/features)
  // no `escaped` list: the Escaped panel is gone — the Features stage's "N escaped" fact links to /features?stage=escaped
  projects?: ProjectsPanel                              // scope = all only
  projectHead?: ProjectHead; projectFeatures?: { rows: FeatureRow[]; total: number }  // scope = one project
}
interface LoopStage { label: 'Tasks' | 'Features' | 'Defects'; total: number;
  parts: Array<{ label: string; n: number; highlighted: boolean }> }   // ONE stacked bar, no legend
// parts in order — Tasks: Ideas, Armed, Queued, Running, Done, Failed
//                  Features: Spec, Building, Shipped, Escaped
//                  Defects: Triage, Open, Closed
// Face = label (fg-muted, 13px/600, not uppercase) · total (22px/700) · bar (6px) · facts line (see Rules · Loop facts).
// Every part stays in the payload and in the bar's accessible name ("Ideas 9, Armed 1, …"); only the facts are printed.
// `highlighted` draws nothing. The 1d per-stage hint is gone: scoped and unscoped loops print the same facts line.
interface RunNextRow { id: number; priority: Priority; status: 'armed' | 'queued'; title: string;
  // `sub` is composed client-side from these: "#38 Span search facets · step 4 of 7" | "Fix for D-91 · #31 …" | "No feature"
  feature_id?: number; feature_title?: string; step?: { n: number; of: number }; fixes_defects: number[];
  action: { kind: 'run' } | { kind: 'wait'; on: number; on_status: TaskStatus } }   // wait rows dimmed
// 1a "Running now" heads the Run next card (same card, its own header above "Run next"); rows on the card's own surface (no agent tint).
// Headers carry no caption sentence: "Running now" · n · "Open Live →"; "Run next" · n · "All in Tasks →".
// Row actions on the Overview are bordered neutral buttons with --color-primary text (▶ Run, Watch) — no info-soft or
//   agent-soft fill; the live marker ("● 3m", agent dot + agent-soft ring) is the only violet. Attribute is the same
//   bordered button with fg-muted text (the sketch's ghost action). Run next / Running rows draw no status or
//   priority glyph: #id · title/sub · action.
// Drawn only while total > 0, on desktop and mobile (2a) alike; an empty block is not drawn. 1d (project scope) follows the same rule.
interface RunningRow { id: number; priority: Priority; status: 'running'; title: string;
  feature_id?: number; feature_title?: string; step?: { n: number; of: number }; fixes_defects: number[];
  // sub = the run-next sub + " · attempt N" when N > 1: "Fix for D-89 · #31 Live tail reconnect · attempt 2"
  live: { run_id: number; trace_id: string | null; attempt: number; started_at: string } | null;  // "live · 12m"
  turns: number | null /* turn_usage rows of the run's trace; null until one is ingested */;
  cost_usd: number | null }                                  // meta "41 turns · $2.10"; unknown parts drop out
// Watch → /trace/sessions/<trace_id>, or /tasks/<id> while the run has no trace
// runNext also carries `ready` (dependency-clear count); mobile draws the same rows as desktop, waiting rows included
interface TriageRow { id: number; round: number; openedAt: string; symptom: string; paths?: string[] }
// Overview decision row: id (fg, 600) · symptom on ONE line, ellipsed · "round R · Sep 15" (faint) under it · Attribute
interface ProjectsPanel {                      // no search box, no sort switch, no legend — the chip's picker owns search
  rows: ProjectRow[] /* ≤5 active projects, attention order */; active: number; quiet: number; total: number }
// header: "Projects" · "{active} active · {quiet} quiet" · "All {total} projects →" (opens the chip's picker)
// active = not quiet; quiet = no escape, no open defect, no open task (completed projects count as quiet)
// active = 0 → the panel is one muted line "All {total} projects quiet" (not drawn by any sketch; operator may veto)
interface ProjectRow { name: string; activity: string /* "active 2h ago" */; stageBar: StageBar;
  features: number; openTasks: number;
  attention: string /* "3 escaped · 4 open defects", warning-strong; zero parts dropped; empty → not drawn */ }
interface ProjectHead { breadcrumb: ['All projects', string]; stageBar: StageBar; shippedPct: number;
  openTasks: number; openDefects: number; spent: string }

// Tasks toolbar (all three views)
interface Toolbar { search: { placeholder: 'Search tasks'; hotkey: '/' }; chips: FilterChip[]; filterButton: true;
  count: string; view: 'board' | 'list' | 'graph' }
interface FilterChip { field: string; value: string /* "High, Urgent"; phone: "High+" */; removable: true }
// Filter menu by reading — next-step list & board: priority (multi), feature steps, tag/source/feature/assignee;
//   flat & legacy-grouped list: + status, archived; graph: the next-step/board set + "Open chains only".
// Graph filters narrow the drawn nodes server-side (same WHERE as the feed); blocking is still judged against
//   every task, and `q` stays the client-side "Find" highlight, never a narrowing.
// `priority` is a comma list server-side (`priority=urgent,high`, canonicalised; unknown member → 400).
// List meta: count + "Group: Next step ▾" (Next step · No grouping · Status · Priority · Project); flat list adds Sort key:dir.
// Board meta: count "23 of 92 tasks" + static "Priority ↓" caption (column order is the server's).

// 1b Board
interface BoardColumn { status: 'idea' | 'armed' | 'queued' | 'running' | 'done'; label: string; n: number;
  hint: string; cards: TaskCard[]; more?: string }
// hints: idea "arm to run" · armed "<k> blocked" · queued "next up" · running "" · done "newest first"
//   (the prototype's "this week" is not drawn: /api/tasks/board's Done column counts every done task)
// more: "11 more ideas" expander (Done's reads "+ N more"); Done's footer "+ N failed · show" — failed is not a column

// 1g / 1h — the board's layout is a function of which columns hold cards (see Rules · Board column layout)
type BoardColumnView =
  | { kind: 'rail'; status: BoardColumn['status']; label: string }   // n = 0: 48px wide, dashed border, status glyph,
                                                                     //   "0", label written vertically (writing-mode vertical-rl)
  | { kind: 'full'; column: BoardColumn }                            // flex 1 1 0 — the full columns share the width;
                                                                     //   cards in a grid repeat(auto-fill, minmax(min(236px,100%), 1fr))
interface QueueClearBanner {                  // 1g: shown above the columns when armed, queued and running are all n = 0
  text: `Queue is clear. Nothing armed, queued or running — ${number} tasks done this week.`   // number = done_week
  actions: ['New task' /* opens the composer, as "c" */, 'Switch to List' /* ?view=list */] }
// /api/tasks/board gains `done_week`: done tasks (same filters as the columns) whose latest run ended in the last
// 7 days, falling back to the task's updated_at when it has no run. It feeds only the banner; the Done column
// still counts every done task.
interface TaskCard { id: number; priority: Priority; status: TaskStatus; title: string; feature?: string;
  fixes?: string /* "fixes D-91" */; waitsOn?: number; live?: string /* "live · 12m", violet ring */;
  attempts?: string /* "2 attempts" */ }                               // waiting cards dimmed

// 1e List — grouped by next step; count reads "38 open · 18 done hidden"; "Group: Next step"
// Drawn flat on the page, not boxed in a card: every row carries a top border, and the rows' left edge
// lines up with the toolbar's search box. Header = chevron · label (13px/600) · n · hint · bulk at the right.
interface ListGroup { key: 'ready' | 'running' | 'blocked' | 'ideas'; label: string; n: number; hint: string;
  bulk?: 'Run all N' /* ready only */; rows: ListRow[]; more?: string /* "11 more ideas · sorted by priority" */ }
// hints: ready "armed or queued, dependencies clear" · running "" · blocked "armed or queued, waiting on another task"
//        ideas "not armed — nothing runs until you arm it"
//   (the prototype's Blocked hint says only "armed"; the text follows the membership rule, which includes queued tasks)
// more: the prototype draws a static line; it is an expander that pages its group (`only=<group>&page=`)
interface ListRow { id: number; priority: Priority; status: TaskStatus; title: string;
  sub: string /* feature · "step 4 of 7" | "attempt 2" */; fixes?: string; waitsOn?: string /* "waits on #408 (running)" */;
  live?: string; action: '▶ Run' | 'Watch' | 'Arm' | null; day: string }
// columns: priority · status · #id · title over sub · facts · action · day
// facts: fixes "fixes D-91" (danger-soft) · waits (warning-soft, dependency-tree glyph — the same glyph on
//   1b cards and 1a Run next) · live "live · 12m" (agent dot with a 3px agent-soft ring)
// action pills: ▶ Run (filled triangle, info-soft) · Watch (agent-soft) · Arm (neutral); a waiting row is
//   dimmed as a whole (opacity .65) and has no action
// Toolbar (all views): the Board · List · Graph switch shows a glyph before each label (columns · list ·
//   dependency tree); the search box and the create button never shrink or wrap
// API row (list and board cards): serialized task + feature_title, step {n,of}, fixes_defects, run_count,
// blocked_by, waits_on {id,status}, live {run_id,trace_id,attempt,started_at}, action 'run'|'watch'|'arm'|null

// 1f Graph — feature lanes
interface GraphLane { feature: string /* "#38" | "no feature" */; title: string /* "Loose tasks" for none */;
  progressPct: number; doneOfTotal: string /* "2 / 5" */ }
interface GraphNode { id: number; title: string; status: TaskStatus; tag: TaskStatus | 'blocked';
  lane: number; column: number /* topological depth */ }              // running: violet ring; done: 60% opacity
interface GraphEdge { from: number; to: number; blocking: boolean }   // blocking = amber dashed; done source = green at 55%
// Edges: orthogonal with 6px rounded bends and an arrowhead at the dependent task; an edge skipping a column turns in
//   the gutters and crosses in the row gap above its target, never through a card. Hovering/focusing a card lights its
//   edges and dims the rest. Cards: opaque surface, soft shadow, 3px status stripe on the left edge (done fades it).
// `after` vs `requires` is not drawn differently (kept as data-kind); "Open chains only" keeps connected
// components holding an open task, then drops lanes with no open task — an edge into a dropped lane goes with it.
interface GraphChrome { find: 'Find a task in the graph'; show: 'Open chains only'; legend: ['blocking','depends on'];
  zoom: ['+', '−', 'FIT']; footer: `${number} chains · ${number} feature lanes hidden (no open tasks)` }

// 1c Features
interface FeaturesTab { stageTabs: Array<{ stage: 'all' | Stage; n: number }>;   // All, Spec, Building, Shipped, Escaped
  sort: 'last_activity'; rows: FeatureRow[]; triageRail: TriageRail }
// stage tabs (chevron strip): inactive tabs all on --color-surface-2, no tinted fills — Spec/Building muted,
//   Shipped success text, Escaped warning-strong text at 600; the active tab stays fg-on-surface
interface FeatureRow { id: number; title: string; ref: string /* tracker id | "no ref" */;
  cost: string /* "≤ $41.75" | "none declared" */; stage: StageCell;
  tasks: { done: number; total: number } /* + progress bar */; rounds: number /* neutral pips, one per round; no red */;
  defects: { open: number; total: number }; lastActive: string; expanded: boolean }
interface StageCell {                         // replaces the 3-node track on desktop rows (mobile 2c cards keep it)
  label: 'Spec' | 'Building' | 'Shipped'      // escaped reads "Shipped"; success colour when shipped/escaped, else fg
  step: '1 of 3' | '2 of 3' | '3 of 3' | 'escaped'   // faint; "escaped" warning-strong at 600
  segs: Array<'muted' | 'success' | 'warning'> }     // 120px, 4px high, 3px gaps, segments share the width:
// spec [muted] · building [muted, muted] · shipped [success ×3] · escaped [success, success, warning]
//   (drawn as the prototype renders it — spec is ONE full-width segment. Its unused g1–g3 hint at three fixed
//    segments with --sf3 empties; whether that was meant is the operator's call, not built)
// expanded: two sections stacked (no longer two columns); titles and symptoms wrap instead of ellipsing.
//   No "Fix tasks" section: a live fix task is already an Active task (listing it twice was defect #193), and
//   each blamed-defect row carries its fix's state
//   "Active tasks · 1 running · 2 armed" — the feature's running + armed + queued tasks
//       header parts are /api/tasks/counts?feature_id= by_status (zero parts dropped); rows from
//       /api/tasks?group=next_step&feature_id= in order Running, Ready, Blocked (the prototype's sample puts a
//       blocked row before a ready one; no rule reproduces that); row = status glyph · #id · title · label
//       label, status-coloured: "running · 3m" (live.started_at) | "armed · waits #415" (waits_on) | "armed" | "queued"
//       more than a group's page → "N more in Tasks →"; none → one muted line (neither drawn by the prototype)
//   "Blamed defects · N open" (id, symptom, "open · fix queued|running" | "open")
interface TriageRail { waiting: number; caption: 'no feature is preselected'; cards: TriageRow[]; focusedId: number;
  focused: { blamePicker: 'Blame a feature…'; keys: { A: 'Accept'; D: 'Dup'; X: 'Decline'; S: 'Snooze' } };
  olderUnattributed: number /* "5 older defects still unattributed · Open →"; Open toggles an in-rail list, since no defects list page exists */;
  fold: TriageFold }
interface TriageFold { folded: boolean /* view-local; survives tab switches (KeepAlive), not reloads */;
  open: { button: 'Fold to sidebar ( ] )' } ;                         // column 1fr 21rem → 1fr 52px, .2s
  strip: { badge: number /* hidden while loading or 0 */; label: 'Triage'; caption: `${number} waiting` | 'unavailable';
           kbd: ']' /* whole strip is one button that unfolds */ };
  key: ']' /* toggles; not from a field, not on another tab; ≥768px only */ }
// Folded: the rail answers no verb and drops an open prompt. Stacked (768–1023px) the strip lies flat.

// Mobile (< 768 px)
interface MobileShell { menuButton: true /* sidebar collapses */; subtitle: ProjectChip /* picker moves under title */;
  tabs: 'full-width segmented control'; minTarget: 44 /* px */ }
// 2a Overview: the quiet loop stacked in its one card (same facts line); loopNote; "Running now" and "Run next" · n · "All in Tasks →" with the same rows and rules as desktop (Watch/Stop/Run ≥ 44 px); the decision summary card sits on --color-surface (no warning tint);
//    Projects rows = name, activity, attention only (no bar, no counts);
//    one summary card "3 defects need a decision · Newest: D-94 · Sep 25" + Triage button → 2d
// 2b Tasks: ALWAYS the 1e list (every non-empty group); Board/Graph not offered; toolbar = Search (folds to a button), chips ("High+ ×"),
//    "+ Filter", Sort (opens the grouping menu); Run action collapses to "▶"; row = status, title, "#id" + facts, action
// 2c Features: stage chips (All, Escaped, Building, Shipped — neutral --color-surface-2, Escaped in warning-strong text) + cards (id, title, active, stage, tasks, defects);
//    plus one "Triage" summary card above the list — the 2d sheet's entry point on Features
//    escaped cards bordered red and listed first, showing their blamed defects
// 2d Triage bottom sheet: "Triage 1 of 3" with ‹ › paging; card (id, round, date, symptom, paths);
//    "Blame a feature · Choose…" ; 2×2 big buttons:
//    The prototype's "Suggested from paths: #38 …" is NOT built: no endpoint proposes a feature and regin never
//    turns a suspect path into a parent. Open question for the operator, not an omission.
//    Accept & create fix task · Duplicate · Snooze · Decline (red)
```

## Rules

### Next action per row

| Row | Condition | Action shown |
|---|---|---|
| task | armed or queued, every dep done | **▶ Run** (POST `/api/tasks/<id>/run`) |
| task | running | **Watch** → the run's live session |
| task | idea | **Arm** → `TaskArmSheet` |
| task | any dep not done | "waits on #N" (+ "(running)" when it is), dimmed, no button |
| task | done / failed | none |
| defect (Overview 1a) | untriaged, unattributed | **Attribute** only — Decline lives in the Features triage rail ("Triage all →") |
| defect (rail focused / 2d sheet) | untriaged | blame picker + Accept/Dup/Decline/Snooze |

### List grouping (1e, 2b)

| Group | Membership | Order | Bulk |
|---|---|---|---|
| Ready to run | status ∈ {armed, queued} ∧ every dep satisfied | priority (urgent first), id | "Run all N" |
| Running | status = running | latest attempt started desc | — |
| Blocked | status ∈ {armed, queued} ∧ some dep unsatisfied | priority (urgent first), id | — |
| Ideas | status = idea | priority (urgent first), id; first 3 then "N more" | — |
| (hidden) | done / failed | counted in "N done hidden" | — |

"Satisfied" is `lib/tasks/deps.py`'s per-edge rule (`requires` → dep done; `after` → dep done or failed; a missing dep task is unsatisfied). Queued-but-blocked tasks sit in Blocked so the four groups partition the open tasks (`open` = sum of `n`). Groups honour every non-status list filter; archived is never grouped.

### Overview ordering

| List | Order | Cap |
|---|---|---|
| Running now | latest attempt started desc (the list's Running group order) — running tasks of every grain | 6; `total` uncapped |
| Run next | dependency-clear first, then priority (urgent first), id — armed/queued tasks of every grain, feature steps included | 6, desktop and mobile alike |
| Needs a decision | untriaged, newest first | 3 |
| Projects (scope all) | **attention**: escaped features desc → open defects desc → last activity desc | 5 active rows; quiet and completed counted in the header, never listed |
| Project features (1d) | last activity desc | 5, "All N in Features →" |

### Scope

| Scope | Overview | Picker row | Create prefill |
|---|---|---|---|
| all | loop + running now + run next + decision + Projects panel | "All projects · portfolio view" ✓ | — |
| one project | scoped loop (same facts line) + breadcrumb head + Features-in-project + 1a panels filtered | project ✓ | project_id |
| none | loop + panels for unscoped work; no Projects panel | "No project" ✓ | — |

### Loop facts (Overview)

| Stage | Facts, in order (a fact whose n = 0 is dropped) | Tone | Link |
|---|---|---|---|
| Tasks | `N running` · `N ready` (Run next's dependency-clear count) · `N failed` | fg · fg · **danger** | none (open question: a failed-tasks link needs a list axis the grouped list lacks) |
| Features | `N building` · `N in spec` · `N escaped` | fg · fg · **warning-strong** | escaped → `/features?stage=escaped` |
| Defects | `N open` · `N to triage` | fg · **warning-strong** | to triage → `/features` (the rail) |

Numbers are fg/600, words fg-muted. Nothing else is printed: ideas, armed, queued, done, shipped and closed live in the bar and its accessible name.

| Bar part kind | Parts | Fill token |
|---|---|---|
| not started | idea, spec | `--color-border` |
| in flight | armed, queued, running, building, open | `--color-fg-faint` |
| settled | done, shipped, closed | `--color-border-strong` |
| needs a human | escaped, triage | `--color-warning` |
| failed | failed | `--color-danger` |

The same fill table colours every stage bar on the Overview: the loop, 1d's breadcrumb head, Projects rows and 1d feature rows.

### Overview colour budget

| Hue | Token(s) | Means on /overview — and nothing else |
|---|---|---|
| blue | `--color-primary` (the blue family: the shell's primary create button keeps its blue fill) | a link or an action's text |
| violet | `--color-agent` (+ `-soft` ring) | the live marker on a running row |
| amber | `--color-warning`, `--color-warning-strong` | a human must decide: escaped, to triage, a project's attention text |
| red | `--color-danger` | failed tasks |
| — | success, info-as-data, orange, every other `*-soft` fill | not used on /overview |

### Triage verbs
The chords move from `1/2/3/4` (`useDefectTriage.js::VERBS`) to **A accept · D dup · X decline · S snooze**, live only while the rail has focus and no text field does. Accept needs a blamed feature (none preselected). On mobile the same verbs are the four sheet buttons; mobile Accept also creates the fix task ("Accept & create fix task").

### Board column layout (1b, 1g, 1h)

| Column state (server `n`, under current filters) | Renders as | Width |
|---|---|---|
| n = 0 | rail: glyph · "0" · vertical label; not clickable, no hint, no expander | fixed 48px |
| Done n = 0 ∧ failed > 0 | full column (header, then the "+ N failed · show" footer) — as a rail the failed tasks would be unreachable | shares the width |
| n > 0 | full column: header (glyph, label, n, hint), card grid, expander | shares the remaining width equally with the other full columns |
| every column n > 0 (1b) | five full columns | equal fifths |
| only one column n > 0 (1g) | four rails + one full column whose grid spreads cards into several columns | rest of the row |

| Banner condition | Banner |
|---|---|
| armed.n = queued.n = running.n = 0 | "Queue is clear." strip (`--color-info-soft` background — the token for the prototype's primary-soft) with New task · Switch to List |
| otherwise | none |

The board area keeps a 360px minimum height; rails stretch to the tallest column. Column order stays Ideas · Armed · Queued · Running · Done, whether a column is a rail or not.
Not drawn by the prototype, therefore asked rather than invented: the board with **all five** columns at n = 0 (e.g. a filter matching nothing) — until the operator decides, it renders five rails plus the banner.

### Viewport

| Width | Tasks views offered | Triage | Nav |
|---|---|---|---|
| ≥ 1024 | Board · List · Graph | right rail on Features | sidebar |
| 768–1023 | Board · List · Graph; rail stacks under the table | stacked rail | sidebar |
| < 768 | List only (`?view=board|graph` renders List) | bottom sheet | menu button |

## Invariants
- I1 Every count is the server's, never a client filter of a loaded page: tab counts, loop parts, "23 of 92", "38 open · 18 done hidden", stage tabs, column/group `n`, "Showing 5 of 32".
- I2 A loop stage's parts sum to its total; Tasks/Features totals equal the tab counts under the same scope.
  The Tasks tab population is every non-archived task, feature steps and fix tasks included (`population_stmt`), so the
  loop's Tasks stage and a project's `open_tasks` count them too; Run next is its own number and is not bound by I2.
- I3 A replaced filter keeps its **default**: archived tasks stay hidden by default (as a chip), done tasks hidden in List.
  Feature steps are **shown** by default — the "Feature steps" box starts checked; unchecking it sends `root_only=1`
  and shows a "No feature only" chip (URL axis `unfeatured=1`).
- I4 `?project=` carries across Overview/Tasks/Features (current `usePipelineScope` behaviour).
- I5 No action is reachable only on hover.
- I6 Light and dark render from `frontend/src/assets/style.css` tokens; no prototype hex values.
- I7 KeepAlive per tab keeps filters, view, selection and scroll across tab switches.
- I8 Mobile tap targets ≥ 44 px.
- I9 A board column with n = 0 is never wider than its 48px rail; the width it frees goes to the columns that hold cards. The one exception is Done while failed > 0 (see Board column layout).
- I10 /overview draws at most the four hue families of the colour budget, and no tinted (`*-soft`) background other than the live marker's ring, in light and dark.
- I11 No loop count is lost: a part not printed as a fact is still in the payload and in the bar's accessible name, and I1/I2 hold unchanged.

## Acceptance

```gherkin
Feature: Shell
  Scenario: three tabs, one-line header
    When I open /pipeline
    Then I land on /overview
    And the header is one row: "Pipeline", project chip, "Updated …", "New task c"
    And the tab strip is exactly Overview, Tasks <n>, Features <n>
  Scenario: retired tabs are gone
    Then the router has no /defects, /projects or /projects/:id entry
    And DefectsView, ProjectsView and ProjectPanelView no longer exist in frontend/src
    When I click a defect id in the triage rail Then /defects/<id> renders inside the shell
  Scenario: hotkeys
    When I press "c" Then the create dialog for the tab opens ("New feature" on Features)
    When I press "p" Then the picker opens with its search focused
    When I press "/" Then the tab search is focused (on Overview, which has no search, the picker opens with its search focused)

Feature: Overview
  Scenario: loop totals
    Then each loop total equals the sum of its parts, and Tasks/Features totals equal the tab counts
  Scenario: quiet loop
    Given 9 ideas, 1 armed, 1 running, 196 done and 2 failed tasks, 1 of them ready
    Then the loop is one card with three columns and no arrows or legend
    And the Tasks facts read "1 running · 1 ready · 2 failed", with only "2 failed" coloured (danger)
    And the Tasks bar's accessible name lists all six parts
    Given no task failed Then the "failed" fact is not drawn
  Scenario: escaped reached through the loop
    Given 3 escaped features
    Then no Escaped panel is drawn and the Features facts end in "3 escaped" (warning-strong)
    When I click "3 escaped" Then /features opens with the Escaped stage tab active
  Scenario: colour budget
    When I open /overview at 1440 px in light and in dark
    Then every chromatic text or fill colour falls in blue, violet, amber or red
    And no element but the live marker's ring has a *-soft background
  Scenario: decision rows
    Then each "Needs a decision" row shows one action, Attribute, and a one-line symptom
    And "Triage all →" opens /features with the rail
  Scenario: projects panel
    Given 8 projects, 1 active and 7 quiet
    Then the header reads "1 active · 7 quiet" and "All 8 projects →" opens the picker
    And the panel has no search box, sort switch or legend
  Scenario: run next
    Given armed #409 depends on running #408
    Then #409 reads "waits on #408", is dimmed and has no Run button
  Scenario: running now
    Given #414 (attempt 2, trace with 14 turns, $0.82) and #408 are running
    Then "Running now · 2" sits above "Run next" in the same card, newest attempt first
    And #414 reads "… · attempt 2", "live · 3m", "14 turns · $0.82" and Watch opens its trace
    And a run with no trace yet shows no meta and Watch opens the task
  Scenario: empty
    Given nothing armed or queued Then "Run next" shows an empty line, not a missing panel
    Given nothing running Then no "Running now" block is drawn
  Scenario: pick a project
    When I pick "regin-core" in the picker
    Then the Projects panel is replaced by the breadcrumb head and "Features in this project" (≤5)

Feature: Tasks
  Scenario: list is grouped by next step
    When I open /tasks
    Then groups Ready to run, Running, Blocked, Ideas render with server counts and one action per row
    And "Run all N" runs every Ready row
  Scenario: board
    When I switch to Board
    Then columns Ideas, Armed, Queued, Running, Done render; failed only behind "+ N failed · show"; "N more" expands a column
  Scenario: sparse board collapses empty columns (1h)
    Given ideas, running and done hold tasks and armed and queued hold none
    When I open /tasks?view=board at 1280 px
    Then Armed and Queued render as 48px rails reading "0" and their label vertically
    And Ideas, Running and Done share the remaining width equally
  Scenario: queue is clear (1g)
    Given only done tasks match, 6 of them finished in the last 7 days
    Then four rails and one Done column render, Done's cards laid out in more than one grid column
    And a banner reads "Queue is clear. Nothing armed, queued or running — 6 tasks done this week."
    When I click "Switch to List" Then the grouped list renders and the URL carries no view= (List is the default, and defaults are not spelled)
  Scenario: graph
    When I switch to Graph
    Then tasks sit in feature lanes; blocking edges are amber dashed; lanes with no open tasks are hidden and counted in the footer
  Scenario: chips
    When I filter priority High and Urgent
    Then a removable chip "Priority High, Urgent" appears and the count updates; removing it clears the URL query

Feature: Features
  Scenario: stage tabs
    Then All/Spec/Building/Shipped/Escaped show server counts and All = sum of the four
  Scenario: expand escaped
    When I expand an escaped feature Then its active tasks and blamed defects are listed, and a running fix task appears once, as an active task
  Scenario: triage keys
    Given the first rail card is focused and no feature picked
    When I press "A" Then Accept is refused until a feature is blamed
    When I press "X" and confirm Then the defect is declined and "waiting" drops by 1
  Scenario: triage rail folds
    When I click "Fold to sidebar" Then the rail is a 52 px strip showing the waiting count and the table widens
    When I press "X" on the strip Then no prompt opens and nothing is posted
    When I press "]" Then the rail unfolds; typed into the search box, "]" is text

Feature: Mobile (390 px)
  Scenario: tasks is the list
    When I open /tasks?view=board at 390 px Then the grouped list renders and no Board/Graph switch is shown
  Scenario: overview matches desktop
    Given #414 and #408 are running and armed #409 waits on #408
    When I open /overview at 390 px Then "Running now · 2" heads the Run next card and #409 shows "waits on #408 (running)"
  Scenario: triage sheet
    When I tap "Triage" on Overview Then a bottom sheet opens at "1 of N" with four 44 px buttons
  Scenario: no overflow
    Then no page scrolls horizontally at 390, 1024 and 1440 px in light and dark
```

### Acceptance tests

| Test | Scenario |
|---|---|
| test_overview_loop_parts_sum_to_totals | Overview · loop totals (I1, I2) |
| test_overview_run_next_orders_dependency_clear_first | Overview · run next |
| test_overview_project_scope_replaces_projects_panel | Overview · pick a project (API) |
| test_overview_running_now_lists_live_attempts_newest_first, test_overview_running_now_caps_rows_and_follows_scope, test_overview_running_now_empty | Overview · running now (API) |
| test_projects_attention_sort | Overview ordering · Projects attention |
| test_features_stage_counts_and_attribution_header | Features · stage tabs (API) |
| test_tasks_next_step_groups | Tasks · list grouped by next step (API) |
| test_pipeline_shell_three_tabs_one_line_header | Shell · three tabs, one-line header |
| test_pipeline_retired_tabs_are_gone | Shell · retired tabs are gone |
| test_pipeline_hotkeys | Shell · hotkeys |
| test_overview_panels_and_empty_states | Overview · running now / run next / empty / pick a project (UI) |
| test_overview_quiet_loop_facts_and_links | Overview · quiet loop, escaped reached through the loop, decision rows, projects panel (UI, `frontend/tests/pipeline-redesign-shell.spec.js`) |
| test_overview_colour_budget | Overview · colour budget (I10) — hue families of computed text/fill colours measured in light and dark |
| test_overview_payload_has_no_escaped_list | Overview · the API no longer serves `escaped`; projects panel carries `active`/`quiet`/`total` (API) |
| test_features_stage_tabs_expand_and_triage_keys | Features · stage tabs, expand escaped, triage keys |
| test_features_triage_rail_folds | Features · triage rail folds |
| test_tasks_list_board_graph_views | Tasks · list, board, graph, chips (`frontend/tests/tasks-views.spec.js`) |
| test_task_board_done_week | Tasks · queue is clear — `done_week` counts runs ended in 7 days, falls back to updated_at, honours filters (API) |
| test_tasks_board_sparse_layout | Tasks · sparse board collapses empty columns, queue is clear (`frontend/tests/tasks-views.spec.js`, rail widths measured) |
| test_pipeline_mobile_layouts | Mobile · tasks is the list, no overflow on /tasks (S3, `frontend/tests/tasks-views.spec.js`); the triage-sheet scenario is 2d's and is added with S2's surface |
| test_pipeline_mobile_overview_features_triage | Mobile · overview matches desktop, triage sheet |
| test_full_suite_passes | Gates · whole pytest + Playwright suite green on the integration branch |

## Gates (build step)
`cd frontend && npx vite build`; Playwright specs for `/overview`, `/tasks` (3 views), `/features`, `/defects/:id`, plus `responsive.spec.js`;
`pytest -n auto --dist=loadfile` over `tests/` for `lib/pipeline/`, `lib/tasks/`, `lib/features/`, `lib/defects/` and the overview endpoint; radon ≥ C; BundleEngine clean on touched `.vue`;
side-by-side screenshots of 1b 1g 1h 1e 1f 1c 2b 2c 2d against the prototype render, and of 1a 1d 2a against `docs/ui-sketches/pipeline-overview-declutter/a-quiet-loop-{light,dark,phone}.png`.

## Work slices

**Base: `f110/integration`**

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|---|
| S1 | Pipeline API | GET /api/pipeline/overview (loop, run next, needs decision, projects panel, project head and features); projects attention sort; feature stage counts and attribution header; tasks group=next_step and per-status board paging | Surface map, Shape (Overview, ListGroup, BoardColumn, FeaturesTab), Rules (Overview ordering, List grouping, Scope), I1, I2 | test_overview_loop_parts_sum_to_totals, test_overview_run_next_orders_dependency_clear_first, test_overview_project_scope_replaces_projects_panel, test_projects_attention_sort, test_features_stage_counts_and_attribution_header, test_tasks_next_step_groups | — | — | f110/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/pipeline tests/tasks tests/features |
| S2 | Shell, Overview, Features | one-line header, project picker, three-tab strip; /overview (1a, 1d); Features tab (1c) with triage rail and A/D/X/S keys; retired tabs, routes and views deleted; mobile 2a, 2c, 2d | Intent, Retired tabs, Shape (ShellHeader, ProjectPicker, Overview, FeaturesTab, TriageRail, Mobile), Rules (Next action, Scope, Triage verbs, Viewport), I3–I8 | test_pipeline_shell_three_tabs_one_line_header, test_pipeline_retired_tabs_are_gone, test_pipeline_hotkeys, test_overview_panels_and_empty_states, test_features_stage_tabs_expand_and_triage_keys | — | S1 | f110/integration | goal-verified-treenav | cd frontend && npx vite build |
| S3 | Tasks views | chip toolbar; Board (1b), grouped List (1e, default), Graph with feature lanes (1f); mobile 2b | Shape (Toolbar, BoardColumn, ListGroup, GraphLane, GraphNode, GraphEdge), Rules (Next action, List grouping, Viewport), I1, I3, I5, I7 | test_tasks_list_board_graph_views, test_pipeline_mobile_layouts | — | S1 | f110/integration | goal-verified-treenav | cd frontend && npx vite build |
| S5 | Board sparse layout | empty columns → 48px rails, full columns share width with a card grid; "Queue is clear" banner; `done_week` on /api/tasks/board | Surface map (1g / 1h), Shape (BoardColumnView, QueueClearBanner), Rules (Board column layout), I1, I9 | test_task_board_done_week, test_tasks_board_sparse_layout | — | S3 | f110/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/tasks && cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/tasks-views.spec.js |
| S6 | Running now (1a) | `running {total, rows}` on /api/pipeline/overview; "Running now" block heading the Run next card, desktop and mobile | Shape (Overview.running, RunningRow), Rules (Overview ordering) | test_overview_running_now_*, test_overview_panels_and_empty_states | — | S1, S2 | f110/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/pipeline && cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/pipeline-redesign-shell.spec.js |
| S7 | Overview declutter (Quiet loop) | loop as one card with a facts line and neutral bars; Escaped panel and `escaped` payload removed; Projects panel without search/sort/legend; decision rows with Attribute only; caption sentences dropped; colour budget on /overview incl. 1d and 2a; closes defect #187 (tints drawn against spec) | Intent (Overview override), Shape (Overview, LoopStage, ProjectsPanel, TriageRow), Rules (Next action, Overview ordering, Scope, Loop facts, Overview colour budget), I10, I11 | test_overview_quiet_loop_facts_and_links, test_overview_colour_budget, test_overview_payload_has_no_escaped_list, test_overview_panels_and_empty_states | — | S2, S6 | f110/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile tests/pipeline && cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/pipeline-redesign-shell.spec.js |
| S4 | integrate + verify | S2, S3, S5 and S7 merged on `f110/integration`; whole pytest and Playwright suites green | Acceptance, Gates | test_full_suite_passes | — | S2, S3, S5, S7 | f110/integration | goal-verified-treenav | .venv/bin/python -m pytest -q -n auto --dist=loadfile && cd frontend && ./node_modules/.bin/playwright test |







