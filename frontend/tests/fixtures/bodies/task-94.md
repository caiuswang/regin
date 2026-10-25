Spec: `regin feature spec 73` — sections **R2**, **R3**, **R4**.

`GET /api/features` accepts exactly one parameter (`q`) and returns **every row in `features`** — no LIMIT, no cursor, no offset. A 2,000-feature install ships 2,000 rows.

- Offset pagination (`Page` in `lib/utils/pagination.py`). Offset, not keyset: the population grows by operator act, and offset is what supports "page N" plus sortable columns — the module's own documented rationale.
- Sort keys: activity (default, gaps last — the existing `_ORDER`), rounds, open defects, escapes, cost, stage, created. Every key tie-broken by `id` so the ordering is total.
- Filters: `stage` (you cannot ask "what is in flight" today) and `project_id`.
- `q` currently matches title and `external_ref` only — extend it to the spec body.
- An unknown **facet value** is refused with 400, not dropped; an unknown **sort key** falls back to the default. `lib/tasks/query.py` already draws this line; the other two tabs must.

Keep the single-CTE shape — no per-row queries (invariant 8).

Acceptance: `test_features_index_is_paginated`, `test_offset_pages_are_disjoint_and_contiguous`, `test_size_above_max_is_clamped_not_rejected`, `test_unknown_facet_value_is_refused_not_dropped`.

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
/Users/taowang/regin/.venv/bin/python /Users/taowang/regin/cli/regin.py task finish 94
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
