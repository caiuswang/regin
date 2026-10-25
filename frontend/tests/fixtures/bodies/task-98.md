Spec: `regin feature spec 73` — sections **R1**, **R5**.

Tasks has the richest controls and the least shareable state.

- **Filters live in localStorage, not the URL** (`useFilterState`, keys `regin.tasks.*`). There is no shareable filtered link and no back/forward. Move them to the URL; the stored value becomes the default on a bare path only.
- **`route.query.project` / `route.query.status` are read once at setup and never watched**, so `/tasks?project=3` works only on a fresh mount.
- **`tag`, `source`, `feature_id` and `assignee` are supported server-side and have no UI** — `tag` is even wired into `params` as a permanently-empty ref.
- **Publish the count to the shell** — TasksView publishes nothing, so the Tasks tab never shows one.
- **Blank-page bug**: `useTaskCounts.load` swallows errors and sets `counts = null`; in grouped mode `groups` is then empty and `empty` is false, so the page renders blank with no error.
- **`selection.visible` accumulates across group loads and is never pruned on collapse**, so "Select all N loaded" can count and act on rows that are no longer rendered.
- **Resolve the dead hierarchy code**: `TaskRows.vue` keeps an `expanded` Set, a `flip()` and passes `:expanded`/`@expand` to `TaskRow.vue`, which has no expand control and never emits it; `nested` is never true. Either implement the parent→run nesting or delete the machinery.
- Group collapse state defaults open and is forgotten on reload, filter change and tab switch — one request per group on every mount.

Keyset pagination stays: tasks is a hot table where rows arrive while you browse.

Acceptance: `test_task_filters_round_trip_through_url`, `test_counts_failure_does_not_blank_the_list`.

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
/Users/taowang/regin/.venv/bin/python /Users/taowang/regin/cli/regin.py task finish 98
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
