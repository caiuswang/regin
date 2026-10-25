Spec: `regin feature spec 73` — sections **Model → The project axis**, **R4**, invariants 4 and 5.

Today only `agent_tasks.project_id` exists (→ `repos.id`). `features` and `defects` carry no project column, so the page cannot be project-scoped at all. This is the schema half of the fix.

- `features.project_id INTEGER NULL` referencing `repos(id)`. **Two edits, not one**: a new `alembic/versions/00NN_*.py` revision AND the matching `db/schema.sql` change, plus the SQLModel field in `lib/orm/models/features.py` — otherwise fresh installs and migrated installs diverge (see CLAUDE.md, Database).
- A defect has **no** project column. Its project is its feature's; an unattributed defect has none. Derivation only.
- CLI surface for declaring a feature's project. **Declared, never inferred** — nothing derives it from `session_repos`, a task's project, a suspect path or a cwd.

`null` is a first-class "no project", not absence — the same semantics `project_id=none` already carries in `lib/tasks/query.py`.

Acceptance: `test_feature_project_null_listed_under_none_scope`, `test_defect_project_follows_feature`, `test_unattributed_defect_has_no_project`.

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
/Users/taowang/regin/.venv/bin/python /Users/taowang/regin/cli/regin.py task finish 92
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
