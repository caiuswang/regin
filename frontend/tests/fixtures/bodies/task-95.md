Spec: `regin feature spec 73` — sections **R3**, **R5**.

- Adopt the shared toolbar (search + stage facet + sort + reset) and the pager.
- Column headers become sort buttons with `aria-sort`; they are plain text today with no control at all.
- **Render `rounds`.** It is already in the `FeatureRow.to_dict()` payload and never displayed — rounds-to-done is the metric the entire pipeline exists to count, and it is invisible on the index.
- **Abandon has no UI anywhere in `frontend/src`.** `POST /api/features/:id/abandon` exists and nothing calls it. Wire it, with a confirm.
- **Adopting a tracker issue is unreachable from the Features tab.** `api_feature_create` supports `ref`, and `DeclareFeatureDialog` is only used from the session-trace surface.
- Give the row a hit target — `tr:hover` styling suggests one today and only the title is clickable.
- `ship()` on the ledger has **no error handling**: `api.post` returns `{ok:false,msg}` on failure and that envelope is assigned straight into `ledger.value`, destroying the page. Add the `ok === false` check its two siblings already have, plus a busy state.

Acceptance: `test_abandon_is_reachable_from_the_ui`, `test_no_list_figure_issues_a_per_row_query`.

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
/Users/taowang/regin/.venv/bin/python /Users/taowang/regin/cli/regin.py task finish 95
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
