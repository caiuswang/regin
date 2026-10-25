# Feature #151 — Task Dependency UI optimization（Task Dependency 界面优化）

## Intent

The two places an operator edits the task DAG are the task detail **Dependencies**
editor and the Tasks **Graph** view. Today the editor is a flat checkbox list that
becomes unreadable once there are more than a few tasks, it does not surface the
current feature, and it gives no visual preview of the result. The Graph view
requires entering an explicit "Edit dependencies" mode before any edge can be
changed, which feels heavy next to tools like Drawio. This feature makes
dependency editing visual, scannable, and direct.

Non-goals:
- No new task-dependency data model or write endpoint; edits still go through
  the existing `PUT /api/tasks/<id>/deps`.
- No change to the lane-graph layout algorithm or the `GET /api/tasks/graph`
  response shape.
- No redesign of Board, List, or Overview.

## Decisions

| # | Decision | Rejected alternative | Why |
|---|---|---|---|
| D1 | The Dependencies editor becomes a split pane: searchable candidate list on the left, live graph preview on the right; stacked on narrow viewports. | Full-screen graph wizard | Operators still need a searchable list when the backlog is large; a graph alone is not enough. |
| D2 | Candidates are grouped by feature, with the current task's feature first, then other features, then loose tasks. | Flat list sorted by id | Requirement: current-feature tasks first. |
| D3 | Each selected row shows an inline two-option toggle for edge kind: **needs it** / **just after**, replacing the `Select`. | Keep the `Select` | Fewer clicks and clearer for a binary choice. |
| D4 | The preview graph shows the current task, its selected dependencies, its direct dependents (one-hop context), and the unpicked open tasks of the task's own feature as dashed "ghost" nodes to click, laid out with the existing `layoutGraph` utility. It re-renders as the selection changes. | Full neighbourhood walk | Direct context is enough to validate the edit and keeps the preview compact. |
| D5 | The preview supports direct manipulation: click an unselected node to add it as a dependency, click a selected dependency node or its edge to remove it. | Read-only preview | Matches the "select arrow/node" ask. |
| D6 | The Tasks Graph view **does not use an edit mode**. Each task node shows Drawio-style connection handles on its left and right edges on hover. | Keep the "Edit dependencies" toggle | The user explicitly asked for Drawio-style handles and no edit-mode gate. |
| D7 | The left handle creates an edge where the hovered task depends on another task (upstream); the right handle creates an edge where another task depends on the hovered task (downstream). | Right = depends-on (shipped first, reported reversed in D319) | Edges run left to right, so what a task waits for sits to its left. |
| D8 | Dragging a handle onto another node creates that edge directly (`requires`); clicking a handle without dragging opens a compact task picker with the same grouped, filterable candidate list as the Dependencies editor, existing edges in that direction checked. | Picker only, or drag only | Drag is the Drawio gesture for an on-screen target; the picker covers off-screen targets and editing several edges at once. |
| D9 | New edges in the picker default to `requires`; a checked row shows the inline **needs it** / **just after** toggle. | Always ask after each click | Less noisy; most new edges are strict dependencies. |
| D10 | Saving the picker sends one or more `PUT /api/tasks/<id>/deps` calls and reloads the graph immediately. There is no pending/save-bar state in the graph itself. | Batch pending edits with a floating save bar | The picker already batches multiple selections per direction; immediate save feels closer to Drawio. |
| D11 | Clicking an existing edge reveals a small × handle at its midpoint; clicking the handle (or the edge hit area) removes the edge and saves immediately. | Right-click context menu | Midpoint handle is discoverable and does not conflict with the node's primary click. |
| D12 | Viewers never see edit controls; the graph and dialog stay read-only for `role === 'viewer'`. | Disable but visible controls | No misleading affordances. |
| D13 | Both dependency dialogs integrate with the pixel pipeline theme through the standard `data-pl-part` system and dedicated CSS in `pixel/dialogs.css`. | Leave them classic-styled under pixel | The user asked for a pixel-specific design for these popups. |

## Shape

```ts
// Endpoints used unchanged
PUT /api/tasks/<id>/deps { depends_on: Array<number | {id: kind}> }
GET  /api/tasks/graph?root=<id>&depth=1   // preview context & server edges
GET  /api/tasks/graph?lanes=1             // lane graph

// Component-level types
interface DepEdge {
  id: number
  kind: 'requires' | 'after'
}
```

## Rules

### R1 Dependencies editor layout

- The dialog keeps the title **Dependencies** and widens to `max-width: 54rem`.
- Left pane (candidates, ~45%) and right pane (preview, ~55%) sit side by side on
  desktop; on narrow viewports the preview renders below the list.
- Candidates come from `useTaskGraph().nodes`, excluding the current task and any
  `done` / `archived` task unless it is already a dependency.
- Candidates are grouped under headers:
  1. **This feature (#N title)** — the current task's `feature_id`.
  2. **Other features**.
  3. **Loose tasks** — tasks with no feature.
- Within a group, sort by task id ascending. Selected rows within a group are
  pinned to the top of that group.
- The existing id/title filter still works; group headers are shown only when the
  group has at least one matching row.
- Each row shows the task status icon/chip, id, and title. Rows already picked
  show the kind toggle; unpicked rows show the status label.
- The preview pane is framed with a title and a subtle background so it no
  longer looks tacked on.

### R2 Selection behaviour in the editor

- Checking a row appends `{ id, kind: 'requires' }` to the picked set.
- Unchecking removes it.
- Switching kind on a picked row mutates the edge in place.
- A "Save dependencies" button still replaces the whole set via
  `PUT /tasks/<id>/deps`.
- Server refusals (cycle, self-edge, duplicate pair, unknown task) render in the
  existing refusal banner naming the offending pair.

### R3 Preview graph inside the editor

- The preview subset is computed from the same `useTaskGraph()` response:
  - the current task;
  - every picked dependency;
  - every direct dependent of the current task (edges where the current task is
    the `depends_on_id`);
  - the unpicked, unsettled tasks of the current task's feature, as ghost nodes
    with no edges — the click targets for "add".
- Dependents and ghosts share a cap of 50 context nodes (dependents first); any
  dependent overflow is reported as "+N more dependents not shown".
- Nodes are placed with `layoutGraph`. Edges point from dependency to dependent:
  - picked dependency → current task;
  - current task → direct dependent.
- Selected dependency nodes and their edges use the primary accent; context-only
  nodes are muted.
- Clicking a ghost node in the preview adds it to the picked set as `requires`.
- Clicking a selected dependency node or its edge removes it from the picked set.
- Clicking a direct-dependent node does nothing; it is context, not editable here.
- Hovering a node highlights its incident edges, reusing the existing hover style.

### R4 Graph view connection handles

- No **Edit dependencies** toggle appears in the graph chrome.
- When `canWrite` is true, hovering or focusing a task node reveals two circular
  `+` buttons:
  - **Left handle** — starts an edge where this task depends on another task.
  - **Right handle** — starts an edge where another task depends on this task.
- Handles are positioned half outside the node so they do not obscure the title.
- Pressing a handle and moving more than 4px starts a drag: a dashed preview
  curve follows the pointer, drawn dependency → dependent, and the node under
  the pointer is highlighted as the drop target. Releasing over another node:
  - left handle of A onto B → `A` deps += `B` (`requires`);
  - right handle of A onto B → `B` deps += `A` (`requires`);
  - the dependent's current edges are re-read first and kept; an existing
    edge is a no-op; the graph reloads on success.
- Releasing over empty canvas or the source node writes nothing and opens nothing.
- A press that never moves past the threshold is a click (mouse or keyboard)
  and opens `TaskEdgePicker` with:
  - `direction: 'depends-on'` for the left handle;
  - `direction: 'blocks'` for the right handle.
- Handle clicks never navigate to the task.

### R5 Task edge picker

- The picker is a modal dialog titled `#X waits for` (depends-on) or
  `Waits for #X` (blocks), with a one-line description.
- It shows the same grouped, filterable candidate list as the Dependencies
  editor, using the same `groupCandidates` helper.
- Candidates exclude the source task and any task that would create an immediate
  cycle for the chosen direction.
- Existing edges in the chosen direction are pre-checked; the user can uncheck
  to remove or check additional tasks to add.
- Checked rows show the inline **needs it** / **just after** kind toggle.
- Saving the picker:
  - For `depends-on`, sends one `PUT /tasks/<source>/deps` with the picked set.
  - For `blocks`, reads each picked dependent's current edges from
    `/tasks/graph?root=<id>&depth=1`, merges the source task in, and sends one
    `PUT /tasks/<dependent>/deps` per picked dependent.
- On success the picker closes and the graph reloads. On refusal the refusal
  message renders in the picker and the dialog stays open.

### R6 Edge removal in the graph

- Each edge has a transparent hit path wide enough to click comfortably.
- Hovering or focusing the hit path reveals a small circular **×** handle at the
  edge's midpoint.
- Clicking the hit path or the × handle removes the edge:
  - reads the dependent's current edges from `/tasks/graph?root=<dependent>&depth=1`;
  - filters out the dependency;
  - `PUT /tasks/<dependent>/deps` with the reduced set;
  - reloads the graph on success.
- While any save is in flight, the graph chrome shows "Saving…" and further
  handle or edge clicks are ignored.
- Refusals render in the graph chrome as a short-lived error line.

### R7 Visual feedback

- Source node hover: the usual lifted shadow; handles fade in.
- Handle hover/focus: primary-coloured fill.
- Edge hover: the × handle fades in; the edge itself keeps its blocking/settled
  styling.
- Removed edges disappear after the successful PUT and graph reload.
- New edges appear after the picker saves and the graph reloads.

### R8 Validation

- Self-edges and cycles are rejected server-side, exactly as today.
- The picker filters out tasks that would create an immediate cycle, but the
  server remains the final authority.
- Direct dependents of the current task cannot be picked in the editor preview;
  the click shows a status line: "A task cannot depend on its dependent." They
  stay in the candidate list (the server refuses the resulting cycle).

### R9 Pixel theme

- In the **pixel** pipeline theme, both dialogs keep the shared `data-pl-part`
  dialog shell (square frame, paper background, inset shadow).
- Internal controls declare the standard parts so the theme can style them:
  - filter `Input` → `data-pl-part="input"`;
  - each row's `Checkbox` → `data-pl-part="check"`;
  - `DepKindToggle` buttons → `data-pl-part="key"`;
  - footer Save → `data-pl-part="key--primary"`, Cancel → `data-pl-part="key"`.
- `frontend/src/pipeline/themes/pixel/dialogs.css` carries the pixel-specific
  overrides for these dialogs:
  - group headers use the pixel font (`Silkscreen`), uppercase, dashed bottom
    rule, and a square count badge;
  - candidate rows are square-framed paper cards with a 2px drop shadow;
  - picked rows gain a 3px left action inset instead of a rounded primary border;
  - the preview pane is framed and the SVG preview uses pixel palette colours
    and the pixel font for IDs/labels.
- Pixel theme tests (`pipeline-theme-tasks.spec.js`) and the task suite under
  the `pixel` project must still pass.

## Acceptance

```gherkin
Feature: Dependency editor redesign
  Scenario: candidates are grouped with current feature first
    Given task T belongs to feature #10
    And there are tasks in feature #10 and feature #20
    When I open Dependencies for T
    Then the "This feature (#10)" group appears before "Other features"
    And selected rows are pinned to the top of their group

  Scenario: preview updates with selection
    Given I opened Dependencies for task T
    When I check task D as a dependency
    Then the preview shows D above T with a "requires" edge
    When I switch D to "just after"
    Then the edge label changes to "after"

  Scenario: preview click toggles a dependency
    Given I opened Dependencies for task T
    When I click an unselected node in the preview
    Then it becomes checked in the list
    When I click a selected dependency edge
    Then it becomes unchecked in the list

  Scenario: save still round-trips kinds
    Given I picked D as "just after" for T
    When I save
    Then PUT /api/tasks/T/deps sends [{id:D, kind:"after"}]
    And the dialog closes

Feature: Graph view dependency editing
  Scenario: add a dependency via left handle
    Given I am on /tasks?view=graph
    When I hover task A and click its left handle
    Then the "#A waits for" picker opens
    When I select task B and save
    Then PUT /api/tasks/A/deps includes B
    And the graph reloads with the new edge

  Scenario: add a dependent via right handle
    Given I am on /tasks?view=graph
    When I hover task A and click its right handle
    Then the "Waits for #A" picker opens
    When I select task B and save
    Then PUT /api/tasks/B/deps includes A
    And the graph reloads with the new edge

  Scenario: drag a handle onto another task
    Given I am on /tasks?view=graph
    When I drag task A's left handle onto task B
    Then PUT /api/tasks/A/deps sends [{id:B, kind:"requires"}] and no picker opens
    When I drag task A's right handle onto task B
    Then PUT /api/tasks/B/deps includes A

  Scenario: drag released on empty canvas does nothing
    When I drag a handle away and release it off any task
    Then no PUT is sent and no picker opens

  Scenario: remove an edge in the graph
    Given I am on /tasks?view=graph
    And an existing edge A → B is shown
    When I hover the edge and click its × handle
    Then PUT /api/tasks/B/deps no longer includes A
    And the edge disappears

  Scenario: viewer cannot edit the graph
    Given I am logged in as a viewer
    When I open /tasks?view=graph
    Then no connection handles appear on hover
```

## Work slices

**Base: current `master`** (no schema changes; no backend slice).

| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Verify |
|---|---|---|---|---|---|---|---|
| S1 | Dependency editor redesign | Cleaner split-pane `TaskDepsEditor`; improved group headers, row styling, and preview framing; kept testids and behaviour. | D1–D5, R1–R3, R8 | `test_deps_editor_groups_current_feature_first`, `test_deps_editor_preview_toggles_selection`, `test_deps_editor_saves_kind` (Playwright) | — | — | `cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/task-deps-editor.spec.js` |
| S2 | Graph Drawio-style handles | `TaskLaneNode` hover handles; `TaskEdgePicker` dialog for both directions; `TaskLaneGraph` wiring; no edit mode. | D6–D12, R4–R7 | `test_graph_edit_adds_dependency`, `test_graph_edit_adds_dependent`, `test_graph_edit_removes_edge`, `test_graph_edit_viewer_hidden`, `test_graph_drag_left_handle_adds_dependency`, `test_graph_drag_right_handle_adds_dependent`, `test_graph_drag_to_empty_canvas_is_a_noop` (Playwright) | — | S1 | `cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/task-graph-edit.spec.js` |
| S3 | integrate + verify | Merge S1 and S2, run full frontend task suite and the relevant API tests. | All | All task specs + `test_dep_kinds` | — | S2 | `cd frontend && npx vite build && ./node_modules/.bin/playwright test tests/task-*.spec.js tests/tasks-*.spec.js` and `.venv/bin/python -m pytest -q -n auto --dist=loadfile tests/tasks` |

## Gates

- `cd frontend && npx vite build` passes for every slice.
- New Playwright specs pass for S1 and S2; existing `task-dep-kinds.spec.js` and
  the rest of the task suite still pass.
- `.venv/bin/python -m pytest -q -n auto --dist=loadfile tests/tasks` passes.
- `vue-complexity` and `frontend-style-convention` rule engines report no
  matches on touched `.vue` files.
- `tests/pipeline-theme-tasks.spec.js` and the task specs under the Playwright
  `pixel` project still pass after the pixel theme styling.
- No schema change; no `regin migrate` required.

## Follow-ups (not built)

- Auto-scroll the viewport while dragging a handle near its edge.
- Bulk dependency editing across multiple selected tasks in the graph.
- Auto-layout of the preview using the lane layout instead of the simple layered
  layout.


