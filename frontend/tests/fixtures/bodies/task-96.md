Spec: `regin feature spec 73` — sections **R2**, **R3**, **R4**, invariant 8.

Carries the one outright **bug** in the report:

**`all` is ordered `id DESC` but `unattributed`, `feature` and `fix-item` are `id ASC`** (`lib/defects/store.py`). Pressing "Unattributed" silently reverses the list, with no column header, no indicator and no control. One direction, every scope.

The rest:
- `store.every()` is unbounded **and** `_serialize` opens its **own `SessionLocal()` per row** on top of `feature_store.get` and `events.last`. That is an unbounded query times an N+1. Paginate (offset `Page`) and hoist the per-row lookups into the one statement.
- The route returns `{items, scope}` with **no `total` and no `has_more`**.
- Add a `status` filter — open/closed is shown on the row and cannot be filtered, so "what is still open" is unaskable.
- Add `q` over `symptom`, and a `project_id` filter derived through the feature.
- Add a `sort` parameter (opened default, updated, round, status, feature), tie-broken by `id`.

No new defect semantics: dedup, write-once blame and the `missing_check`-on-close requirement stay exactly as `lib/defects/store.py` enforces them.

Acceptance: `test_defects_scope_switch_keeps_sort_direction`, `test_defects_index_is_paginated`, `test_no_list_figure_issues_a_per_row_query`.

---

## Landing protocol — read before you commit

You are in **your own git worktree** on your own branch. Three other agent
sessions share the main checkout at `/Users/taowang/regin`; do not touch it.

The integration branch for this feature is **`f73/integration`**. Your
dependencies' work is already merged into it — that is why your branch forked
from it — and your work must land there before anything downstream starts.

As your final act, in this order:

```bash
git add -A && git commit           # on your own branch
git merge f73/integration          # take anything that landed while you worked
                                   # (resolve conflicts here, in your worktree)
.venv/bin/python -m pytest -q -n auto --dist=loadfile   # green before landing
git push . HEAD:f73/integration    # land it; no checkout of the branch needed
```

**Then, and only then:**

```bash
/Users/taowang/regin/.venv/bin/python /Users/taowang/regin/cli/regin.py task finish 96
```

`task finish` is what fires the dependent slices, and a dependent forks a fresh
worktree from `f73/integration` at launch. Landing after you report would hand
the next slice a branch without your work in it.

## If you need the regin server restarted

**Do not restart it yourself.** Every agent session is a child of `regin serve`;
restarting it from inside kills you mid-run and takes the other sessions with
it. A detached coordinator outside that process tree does it for you:

```bash
echo restart > ~/.regin-coord/request.fifo
```

It refuses while unrelated sessions are live (`restart force` overrides), and it
revives your run afterwards under the same trace. A revived run must still call
`regin task finish` itself — that is the only thing that continues the cascade.

Nothing in this repo needs a restart to run tests. You need one only to see a
Python change take effect in the live server on :8321.
