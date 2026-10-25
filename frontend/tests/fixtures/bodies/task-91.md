Spec: `regin feature spec 73` — sections **Model → The list, as one type**, **R1**, **R5**.

Deliver the primitives every tab then adopts. Nothing user-visible changes in this slice; it is the contract the other nine depend on.

- A composable that serializes/deserializes `ListQuery` (project, q, sort, page/cursor, size, facets) to and from `location.search`. URL always wins; localStorage supplies a default on a bare path and is then **rewritten into the URL**; page/cursor are never restored from storage.
- One list toolbar component: search + facets + sort + reset, in that order.
- One sortable table header: a `button` carrying `aria-sort`, click toggles direction.
- One empty-state component — copy is per scope, chrome is not. Three near-identical implementations exist today across the three tabs.
- One date formatter used by every tab (`shortDate` in `frontend/src/utils/ledgerFormat.js`). Features and Defects render the same column two different ways today.

Acceptance: `test_task_filters_round_trip_through_url`, `test_bare_path_rewrites_stored_filters_into_url`, `test_filter_change_resets_page_and_replaces_history`, `test_every_tab_uses_the_shared_date_formatter`.

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
/Users/taowang/regin/.venv/bin/python /Users/taowang/regin/cli/regin.py task finish 91
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
