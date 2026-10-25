Spec: `regin feature spec 73` — section **Acceptance** (22 named tests).

Playwright specs for the browser-level Gherkin, pytest for the API-level items. Red first — a test never seen fail is not evidence.

Groups: ordering (the scope-switch reversal), pagination (bounded, disjoint, contiguous, clamped), project scope (null feature, defect-follows-feature, unattributed, and the property that no row is invisible under every scope), URL state (round-trip, bare-path rewrite, refused facet, replace-not-push), shell (three counts, counts-failure, tab round-trip, detail keeps the strip), consistency (dead link, one formatter, permalink, confirm, client-side guard, abandon reachable, no per-row query).

Gates before this slice is done:
```
.venv/bin/python -m pytest -q -n auto --dist=loadfile
cd frontend && ./node_modules/.bin/playwright test
```
The full suite, not a subset — S1 touches `lib/orm/` and a serialized shape.

**Operating constraint**: do not restart the backend serving the session doing the work. Verify against a second instance on its own port and its own copy of the DB.

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
/Users/taowang/regin/.venv/bin/python /Users/taowang/regin/cli/regin.py task finish 100
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
