## Problem

The task Dependencies dialog lists every other task as a candidate blocker, including tasks that are already done. A done task can never block a run, so those rows are noise — and on a long list the real candidate is hard to find.

## Spec

* The candidate list omits tasks that can no longer block: `done` and `archived`. A task that is already ticked stays visible whatever its status, so an existing edge can still be removed.
* The dialog has a text filter that narrows the candidate list by id or title as the user types. Ticked rows are never hidden by the filter.
* When the filter matches nothing the dialog says so instead of showing an empty box.
* The saved dependency set is unaffected: ticked-but-hidden rows are still submitted.

## Acceptance (Playwright, `frontend/tests/tasks-and-keys.spec.js`, "/tasks — the dependency picker")

* `done and archived tasks are not offered as blockers`
* `a done task already ticked stays listed so it can be unticked`
* `the filter narrows candidates by id or title, and keeps ticked rows`
* `an unmatched filter says so`