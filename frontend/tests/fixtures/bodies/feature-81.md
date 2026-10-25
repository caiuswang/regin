# Typed dependency edges — an order is not a dependency

## Intent

`agent_task_deps` carries one edge shape and it is asked to mean two
different things.

Sometimes B genuinely consumes A: A cuts the branch B builds on, A writes the
schema B migrates, A is the slice B verifies. If A fails, B is meaningless and
must not run.

Sometimes B has nothing to do with A at all. The edge was added for one reason
only — so an operator can arm a pile of unrelated work and walk away, and the
tasks go one at a time instead of eight agents landing on the same tree at
once. If A fails, B is *still* meaningful, and stalling it teaches the operator
nothing except to stop chaining work.

Today both are written the same way, so the graph cannot tell them apart and
resolves every edge with the strict rule. A failed slice parks a queue of work
that never depended on it, and only a human re-arming the failed task releases
it.

The fix is to say which one the edge is. The edge grows a `kind`.

## Non-goals

- No change to acyclicity: both kinds are edges, and a cycle through either is
  still refused at write time.
- No branch/worktree inheritance. A run's base branch is armed explicitly
  (`params.worktree.base`) and is not derived from the graph; `kind` does not
  change that.
- No scheduler, no interval, no retry. Firing stays event-driven off the
  completion that could change the answer.
- No third kind. `archived` dependencies, optional edges and "run even if
  nothing finished" are not in scope.

## Types

```
DepKind   = "requires" | "after"          -- default "requires"
DepEdge   = { id: TaskId, kind: DepKind }
AgentTaskDep = { task_id, depends_on_id, kind }   -- PK (task_id, depends_on_id)

-- wire, PUT /api/tasks/<id>/deps
DepsBody  = { depends_on: (TaskId | DepEdge)[] }
DepsReply = { task_id, depends_on: TaskId[], deps: DepEdge[] }

-- wire, GET /api/tasks/graph
GraphEdge = { task_id, depends_on_id, kind }
```

`kind` is its own column, not a second list and not a flag folded into the id.
Two lists would let one id appear in both and make "which kind is this edge"
unanswerable; a boolean would have to be replaced the first time a third
ordering rule appears.

In the slice table the same edge is a `SliceDep` — a `str` subclass carrying
`.kind`. A subclass rather than a parallel `{slice: kind}` map because every
reader of that cell (the unknown-id check, the cycle walk, the tail's leaf
set) wants the id and nothing else, and one object cannot drift apart the way
a list and a lookup table beside it can.

`as_edges` is the Python-caller coercion in front of `set_deps`, so the four
in-tree callers that pass bare ids need no change. A bad kind there raises
plainly rather than as a `DepRejected`, whose `pair` would have to be invented
— only the HTTP body produces a refusal a caller can act on.

## The rule

A dependency is *satisfied* — it no longer holds its dependent back — when:

| dep status | `requires` | `after` |
|---|---|---|
| `idea` / `armed` / `queued` / `running` | waits | waits |
| `done` | satisfied | satisfied |
| `failed` | waits, **and parks** | satisfied |
| `archived` | waits | waits |

`requires` waits for *success*. `after` waits for the task to be *settled* —
to have stopped, whichever way. That single row is the whole feature; every
other change below is a consequence of it.

And the two have different *lifetimes*. A `requires` edge is a fact about the
work — what this task was built on — so it stays in the graph after it is
met, because the graph is where that is recorded. An `after` edge is not a
fact about anything: it says "do not start until that one has stopped", and
the moment that one stops the sentence is true forever. Keeping the row would
leave the DAG silting up with orderings that describe no relationship, which
is the clutter this kind exists to stop drawing. So it is **retired the
moment it is satisfied** — including when it is born satisfied, behind a
dependency that had already settled before it was drawn.

"Parks" is the second axis: a `failed` `requires` dependency is not a wait that
will end, so the operator is told which slice stopped the chain
(`task.chain_stopped`). A `failed` `after` dependency stops nothing and raises
no card — the queue moves on, which is what ordering-only was for.

## Invariants

1. An `after` edge never appears in `failed_blockers()`, and therefore never
   raises `task.chain_stopped`.
2. `blocking(t)` ⊆ `depends_on(t)` for both kinds; the kind narrows the set, it
   never adds to it.
3. Acyclicity is computed over the union of both kinds. Changing an edge's kind
   can never turn a legal graph into a cycle or back.
4. An edge written with no kind is `requires`. Every row that existed before
   this change is `requires`, because that is the rule the graph was resolved
   under when those edges were drawn.
5. `set_deps` stays a replacement, and the replacement covers the kind: the
   stored kind is what the last PUT said, never a merge with what was there.
6. The same (task, dep) pair cannot be sent twice with two kinds. Refused by
   pair, like a self-edge.
7. A stored `after` edge is always one that is still holding its dependent
   back. Retirement is a garbage collection of edges `blocking()` already
   ignores, never a second opinion about them — if the two disagree,
   `blocking()` decides and nothing hangs.
8. Retirement runs *after* the firing decision, never before. Sweeping first
   would leave a task whose only edge was born satisfied with no dependencies
   at all, and `fire_if_ready`'s "nothing could ever come due" guard would
   send it home unlaunched.
9. Every writer of an `after` edge retires: the deps route, arming, and
   transcription. A writer that skips it is a hole that puts back the clutter
   — `set_deps` replaces the whole set, so re-reading an unchanged slice table
   would resurrect every ordering already honoured.

## Behaviour

```gherkin
Scenario: an ordering-only chain survives a failure
  Given task B is armed and depends on task A with kind "after"
  When A's run settles failed
  Then B starts
  And no task.chain_stopped card is raised

Scenario: a real dependency still parks on a failure
  Given task B is armed and depends on task A with kind "requires"
  When A's run settles failed
  Then B stays armed
  And a task.chain_stopped card names A

Scenario: an honoured ordering leaves the graph
  Given task B depends on task A with kind "after"
  When A's run settles, succeeded or failed
  Then the edge B → A is gone from the graph
  And a "requires" edge in its place would still be there

Scenario: an ordering drawn behind something already finished never lingers
  Given task A has already settled
  When PUT /api/tasks/B/deps draws B → A with kind "after"
  Then B starts
  And the reply's deps is empty, because nothing was left to keep

Scenario: an ordering still owed is kept
  Given B depends on running task A and on settled task C, both "after"
  Then the edge B → C is gone and the edge B → A remains

Scenario: both kinds still wait for an unfinished dependency
  Given task B is armed and depends on running task A with kind "after"
  Then blocking(B) contains A

Scenario: an id sent bare is a real dependency
  When PUT /api/tasks/B/deps is sent {"depends_on": [A]}
  Then the stored edge B → A has kind "requires"
  And the reply's deps is [{"id": A, "kind": "requires"}]

Scenario: a kind is round-tripped
  When PUT /api/tasks/B/deps is sent {"depends_on": [{"id": A, "kind": "after"}]}
  Then GET /api/tasks/graph returns edge B → A with kind "after"

Scenario: an unknown kind is refused, not coerced
  When PUT /api/tasks/B/deps is sent {"depends_on": [{"id": A, "kind": "maybe"}]}
  Then the response is 400 naming the field depends_on

Scenario: one pair cannot hold two kinds
  When PUT /api/tasks/B/deps is sent
       {"depends_on": [{"id": A, "kind": "after"}, {"id": A, "kind": "requires"}]}
  Then the response is 400 naming the pair B → A

Scenario: a spec's slice table can declare ordering
  Given a slice row whose depends_on cell reads "S3 (after)"
  When the feature is transcribed
  Then the task for that row depends on S3's task with kind "after"

Scenario: the drawing distinguishes them
  Given the DAG panel shows a "requires" edge and an "after" edge
  Then the "after" edge is dashed and the "requires" edge is solid
  And the legend says which is which
```

## Surfaces

| Surface | Change |
|---|---|
| `db/schema.sql` + alembic `0031` | `agent_task_deps.kind TEXT NOT NULL DEFAULT 'requires'` |
| `lib/orm/models/tasks.py` | `AgentTaskDep.kind` |
| `lib/tasks/deps.py` | `DepEdge`, kind-aware `blocking` / `failed_blockers` / `graph`; `set_deps` still accepts bare ids |
| `lib/tasks/runs.py` | `_announce_broken_chains` reads only `requires` blockers; `fire_if_ready` / `fire_ready_dependents` retire what they honoured |
| `web/blueprints/tasks.py` | `PUT /deps` parses kinds and returns the edges that SURVIVED the write, re-read rather than echoed |
| `lib/tasks/slice_table.py` | a `depends_on` cell item may read `S3 (after)` |
| `lib/tasks/transcribe.py` | carries the parsed kind into `set_deps`, then retires — re-transcription must not resurrect an honoured ordering |
| `frontend` — deps editor, graph layout, graph panel | per-edge kind control; dashed `after` edge + legend |

## Acceptance items

`tests/tasks/test_dep_kinds.py`

- `test_bare_id_is_a_requires_edge`
- `test_object_form_stores_its_kind`
- `test_unknown_kind_is_refused`
- `test_one_pair_with_two_kinds_is_refused_by_pair`
- `test_set_deps_replaces_the_kind_too`
- `test_blocking_keeps_a_failed_requires_dependency`
- `test_blocking_drops_a_failed_after_dependency`
- `test_blocking_keeps_an_unfinished_after_dependency`
- `test_failed_blockers_ignores_after_edges`
- `test_graph_edges_carry_their_kind`
- `test_cycle_through_an_after_edge_is_still_refused`
- `test_api_put_deps_round_trips_kinds`
- `test_api_put_deps_rejects_an_unknown_kind`
- `test_failed_after_dependency_fires_its_dependent`
- `test_failed_after_dependency_raises_no_chain_stopped_card`
- `test_failed_requires_dependency_still_parks_its_dependent`
- `test_a_satisfied_after_edge_is_retired_when_its_dependency_settles`
- `test_an_after_edge_behind_a_failed_dependency_is_retired_too`
- `test_a_requires_edge_survives_its_dependency_settling`
- `test_an_unsatisfied_after_edge_is_kept`
- `test_an_after_edge_written_behind_a_settled_dependency_is_retired_on_write`
- `test_the_graph_stops_drawing_a_retired_after_edge`
- `test_a_done_dependency_satisfies_either_kind`
- `test_api_put_deps_rejects_a_pair_sent_with_two_kinds`

`tests/tasks/test_slice_table.py` / `test_transcribe.py`

- `test_depends_on_cell_reads_an_after_marker`
- `test_an_unknown_dependency_kind_is_refused_by_row_and_column`
- `test_transcribe_writes_the_declared_dep_kind`
- `test_retranscribing_does_not_resurrect_an_honoured_after_edge`

`frontend/tests/task-dep-kinds.spec.js`

- `after edges draw dashed and requires edges draw solid`
- `the deps editor saves an ordering-only edge`


