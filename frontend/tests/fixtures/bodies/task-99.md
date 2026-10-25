Spec: `regin feature spec 73` — section **R5**, `test_feature_figures_defect_link_lands_scoped`.

**Confirmed dead link**: `frontend/src/components/features/FeatureFigures.vue` builds the defects tile as `{ path: '/defects', query: { feature: l.key } }`. Two faults in one line — `l.key` does not exist in the ledger payload (`Ledger.to_dict()` emits `id`), and `DefectsView` reads `feature_id`, not `feature`. The tile silently lands on the unscoped All list.

Then close the remaining one-way streets:
- **No Features → Tasks link anywhere on the index** — no task count column, no `/tasks?feature_id=` per row, though the filter exists and `FeatureTasks.vue` already uses it.
- A feature's **sessions count is never clickable** through to its traces.
- A defect's **fix item** is displayed and does not link to the task.

Rule: every link a Pipeline row or figure offers lands on the scope it names.

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
/Users/taowang/regin/.venv/bin/python /Users/taowang/regin/cli/regin.py task finish 99
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
