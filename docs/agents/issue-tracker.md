# Issue tracker

A feature is a **`features` row** with an integer id. It is either native
(`origin = 'native'`, regin is the tracker) or adopted from Linear
(`origin = 'linear'`, `external_ref` = the issue key, e.g. `CAI-274`).
`(origin, external_ref)` is unique, so one issue backs at most one row.

**Linear** — team `Caiuswang`, project `regin` — is reached through the
`linear-server` MCP tools (`get_issue`, `list_issues`, `save_issue`,
`list_comments`, `save_comment`). There is no `gh issue` / `glab issue` path
here, and no `.scratch/` markdown convention.

| Verb | Native | Linear |
|---|---|---|
| declare | `regin feature new "<title>" --spec …` | `regin feature new --ref CAI-274` (title comes from the fetch) |
| address | `<id>` | `<id>`, or `--ref CAI-274` on any `feature` verb |
| read stage | `regin feature stage <id>` | same; `regin feature refresh <id>` folds the issue's current state into the log once |

Remote state is pulled by `refresh`, an event: it appends a row only when the
mapped state differs from the last one, and is never a poll.

**A feature is never moved between trackers.** There is no verb for it. If you
want the other tracker, declare a fresh feature and leave the old ledger intact.

## PRs as a request surface

Off. Pull requests are not part of the triage queue.

## Where a spec lives

Native: on the row, `features.body` — `regin feature spec <id>` reads it,
`regin feature spec <id> --file <path>` replaces it in place (no revision
history). Linear: the issue description. A spec is never copied into `docs/`.

## Tasks under a feature

A spec is cut into slices; each slice is an `agent_tasks` row carrying
`feature_id`, with dependency edges in `agent_task_deps`
(`regin task transcribe`, or `PUT /api/tasks/<id>/deps`). A task's `status` is
the run lifecycle: a run settling `done` means *this session finished*, never
*this feature shipped*.

## The done-claim

The done-claim is the boundary every later defect is blamed against, and only
an explicit act writes it:

* **regin**: `regin feature ship <id>` appends a `shipped` event to
  `feature_events`. It writes the database directly, so it works with the
  server stopped.
* **Linear**: move the issue to a state of type `completed`, then
  `regin feature refresh <id>` records it.

`claimed_at`, `stage` and `rounds` are queries over that append-only log. The
walk-back is `regin feature reopen <id>` (+1 round); `regin feature abandon <id>`
takes a feature down at no round.

## Wayfinding operations

How the `wayfinder` skill's concepts map onto each tracker:

| Concept | Linear | regin |
|---|---|---|
| map | an issue labelled `wayfinder:map` | a feature |
| ticket | a **child issue** of the map (`parentId`) | a task with that `feature_id` |
| ticket type | `wayfinder:research` / `wayfinder:prototype` / `wayfinder:grilling` / `wayfinder:task` | the same words, as task tags |
| blocking | Linear's native relation — `blocks` / `blockedBy` on `save_issue` | `agent_task_deps`, via `PUT /api/tasks/<id>/deps` |
| closed | a state of type `completed` | the feature's `shipped` event |
