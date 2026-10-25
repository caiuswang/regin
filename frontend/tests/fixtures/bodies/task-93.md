Spec: `regin feature spec 73` — section **R6**, and **Model → The shell**.

Four defects in `frontend/src/views/PipelineView.vue`:

1. **No shared scope.** Add the project selector above the tab strip; it is the one axis that carries across tabs.
2. **Counts render only for the active tab** — the one case where a tab count carries no information, since you already know what you are looking at. Publish all three from one counts request, under the current project scope. TasksView publishes nothing at all today.
3. **`:key="$route.path"` remounts the view on every tab switch**, dropping filters, selection, open groups and scroll. Keep the leaving tab's state and restore its scroll on return.
4. **`/features/:id` is a sibling route, not a child**, so opening a feature detail makes the whole tab strip disappear. Nest it; keep Features marked current.

Also: a failed counts request must not blank the list — today `useTaskCounts.load` swallows the error and sets `counts = null`, so grouped mode renders an empty page with no error.

Acceptance: `test_shell_publishes_all_three_counts`, `test_counts_failure_does_not_blank_the_list`, `test_tab_roundtrip_preserves_query_and_scroll`, `test_feature_detail_keeps_the_tab_strip`.

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
/Users/taowang/regin/.venv/bin/python /Users/taowang/regin/cli/regin.py task finish 93
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
