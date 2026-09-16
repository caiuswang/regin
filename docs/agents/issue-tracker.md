# Issue tracker

A feature is a **tracker issue**, and the tracker is either Linear or regin
itself. `feature_key` is the identifier, and it is stable from the first day of
the spec.

**Linear** — team `Caiuswang`, project `regin` — is reached through the
`linear-server` MCP tools (`get_issue`, `list_issues`, `save_issue`,
`list_comments`, `save_comment`). There is no `gh issue` / `glab issue` path
here, and no `.scratch/` markdown convention.

**regin's own tasks** are the other tracker. A top-level `agent_tasks` row — one
with no `parent_id`, that something names as parent — is a feature, addressed
`regin:<id>` (e.g. `regin:42`). Its child tasks are the runs. This is what lets
an install with no Linear credential spec, build, ship and read the ledger of a
feature end to end; Linear's free tier caps issue count, and reaching it used to
stop regin declaring a feature at all.

regin's code reaches both through `lib/tasks/integrations/registry.py`, which
resolves a pasted reference by provider *claim* and refuses an ambiguous one.
The two claim sets are disjoint by construction — a colon defeats Linear's
`fullmatch` — so no paste is ever ambiguous between them.

**A feature is never moved between trackers.** There is no verb, no button, no
alias and no second link, in either direction. `feature_key` is identity: a
rewrite would re-attribute write-once `defects.blamed_against_state` to a task
that never held that state, orphan branch names already rooted on the old key in
git history, and strand an `issue_cache` row. If you want the other tracker,
declare a fresh feature and leave the old ledger intact.

## PRs as a request surface

Off. Pull requests are not part of the triage queue.

## Where a spec lives

In Linear, in the issue description. In regin, in the task's `body`, which the
authenticated edit path bounds at 64,000 characters and **refuses** past it
rather than truncating. The capture surface — third-party intake, reachable by a
machine key — keeps its 8,000-character bound.

A top-level task mints its key on creation and reads stage `spec` before any
session exists. It is deliberately **invisible in the feature index** until it
has its first child: the index admits a key only when something names it as
parent, because admitting every root task would be a filter deciding which tasks
are really features.

## The done-claim

Moving a feature to Done is the **done-claim**, and it is the boundary every
later defect is blamed against. Nothing derives it — a run settling `done` means
*this session finished*, never *this feature shipped*.

* **Linear**: move the issue to a state of type `completed`.
* **regin**: `regin task ship <id>`, which writes the database directly and
  therefore works with the server stopped. It records the claim in
  `agent_tasks.claimed_at`, **not** in `status` — `status` is the task
  lifecycle and a run settling writes `done` onto it, so a feature read off
  that word would ship itself the moment its own run finished. It is refused on a task with a
  `parent_id` or one holding runs — both are the run grain, not the feature
  grain. A **childless** task is claimable; that is how a spec-only feature
  ships.

There is no `regin feature ship`: a verb that worked only for `regin:*` would be
a caller branching on provider.

The un-claim is `done → idea` from the edit surface, and it costs no round.

## Wayfinding operations

How the `wayfinder` skill's concepts map onto each tracker:

| Concept | Linear | regin |
|---|---|---|
| map | an issue labelled `wayfinder:map` | a top-level task tagged `wayfinder:map` |
| ticket | a **child issue** of the map (`parentId`) | a **child task** (`parent_id`) |
| ticket type | `wayfinder:research` / `wayfinder:prototype` / `wayfinder:grilling` / `wayfinder:task` | the same words, as tags |
| claim | `assignee` set to the dev driving the map | `assignee`, set on the edit path only |
| blocking | Linear's native relation — `blocks` / `blockedBy` on `save_issue` | `agent_task_deps`, via `PUT /api/tasks/<id>/deps` |
| closed | a state of type `completed` | `claimed_at` set — the done-claim |
| resolution | a comment on the ticket (`save_comment`) | a comment thread on the task |

Frontier — the open, unblocked, unclaimed children of a map:

```
list_issues(parentId="CAI-230", state="Backlog", fields=["id","title","status","assignee"])
```

then drop any whose `blockedBy` (via `get_issue(..., includeRelations=true)`)
still holds an open issue. Linear renders the same thing visually in the issue's
relations panel, which is why blocking uses the native relation rather than a
body convention.

Issues need ids before they can reference each other, so create every ticket
first and wire the blocking edges in a second pass.

The regin equivalent is `GET /api/tasks?parent_id=<id>`, with `root_only=1` for
the features themselves. Note the grains do not mix: a dep edge between a parent
and its own child is refused, because "waits for" and "is a run of" are two axes
and one pair cannot carry both.
