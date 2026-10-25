Spec: `regin feature spec 73` — section **R5**.

The Defects tab has no filter bar, no search, no keyboard support, no confirm dialog and no bulk actions — two hand-rolled scope pills are the entire control surface.

- Adopt the shared toolbar (status + scope + search + sort) and `PageControls`. It uses none of `PageControls.vue`, `ConfirmDialog.vue`, `FlashMessage.vue`, `ui/Tabs.vue` or `ui/Select.vue` today.
- **`/defects/:id` does not exist.** A defect has no detail route and its id is not linkable — every row needs a permalink.
- **Reopen confirms nothing** and it bumps the round. Route it through `useConfirm`.
- `missing_check` is surfaced only as a placeholder and is **not validated client-side** — an empty submit posts and relies on the server's 400. Attribute likewise accepts any `Number(value)`; a non-numeric becomes `NaN`.
- The `fix_item` scope renders **no banner at all** — an empty header with no statement of what you are looking at.
- Dates render raw; use the shared formatter.
- After every write the whole list flips to `Loading…` and **scroll position and any open form are lost**. Reload the current page under the current query instead (invariant 7).

Still no bulk attribute — one defect, one assertion.

Acceptance: `test_defect_has_a_permalink`, `test_reopen_asks_for_confirmation`, `test_close_without_missing_check_is_refused_client_side`.

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
/Users/taowang/regin/.venv/bin/python /Users/taowang/regin/cli/regin.py task finish 97
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
