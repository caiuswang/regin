# Multi-User Topics (Standalone Mode)

regin's default mode is standalone: each user runs their own instance
against their own local SQLite. This page describes how approved topics
travel between users **without a shared database** — git carries the
authoritative state and each user's local DB is a cache of what those
files say.

For the broader proposal pipeline (drafts, evidence, review states),
see [`docs/topics/proposals.md`](proposals.md). The wiki at
`.regin/topics/wiki/topic-proposal-pipeline.md` covers the same flow at
the implementation level.

## What ships through git, what stays local

| Artifact | Wire format | Shared via git? |
| --- | --- | --- |
| Approved graph | `.regin/topics/topic.json` | yes (force-added by `pre-commit`) |
| Per-topic wiki | `.regin/topics/wiki/<topic_id>.md` | yes (normal tracked file) |
| In-flight proposals (drafts, revisions, feedback threads) | `.regin/topics/bundles/<id>.json` | opt-in, via `proposal-export` |
| Per-run artefact dirs `.regin/topics/proposals/<id>/` | — | no, `.regin/` is gitignored |
| `GraphSnapshot`, `ProposalRun`, `ProposalRevisionTopic`, audit log | — | no, local SQLite only |

The contract: **approved knowledge** flows between users automatically;
**in-flight work** flows only when a producer explicitly exports a
proposal bundle. Live co-editing of a single proposal draft still
requires the shared-server model.

## Sharing an in-flight proposal

A proposal bundle is one JSON file carrying a run's full review state:
run status/metadata, every revision (kind, stamps, topic snapshots
including accept/ignore markers), the combined wiki, and all feedback
threads with their comments, anchors, and resolution states. Numeric
SQLite ids never cross machines — revisions travel by
`revision_number` and every FK is rebuilt at import.

Producer:

```bash
regin topics proposal-export 20260519T173635Z
git add .regin/topics/bundles/20260519T173635Z.json
git commit -m "share proposal for review"
```

Consumer (after `git pull`):

```bash
regin topics proposal-import .regin/topics/bundles/20260519T173635Z.json
```

The import seeds the run into the consumer's local SQLite; review then
continues locally in the WebUI or via `proposal-show` /
`proposal-feedback` / `proposal-apply`. Importing never touches the
approved graph and marks nothing applied. If a run with the same id
already exists locally, the import refuses; `--force` replaces the
local run (revisions + feedback included) wholesale — use it to pick up
a re-exported bundle after another round of edits.

## Data flow on a `git pull`

```
   user A                  git                 user B
  ┌─────────┐            ┌──────┐            ┌─────────┐
  │ approve │ pre-commit │topic │  git pull  │ post-   │
  │ (apply) │ ─────────► │.json │ ─────────► │ merge   │
  │   ↓     │  force-    │  +   │            │ hook    │
  │ ORM +   │  add       │wiki/ │            │   ↓     │
  │ disk    │            │      │            │ regin   │
  │         │            │      │            │ topics  │
  └─────────┘            └──────┘            │ import  │
                                              │   ↓     │
                                              │GraphSnap│
                                              │(ORM)    │
                                              └─────────┘
```

1. **User A approves.** `apply_diff` writes a new `GraphSnapshot` row
   (ORM, authoritative) and atomically exports `topic.json` + per-topic
   wikis to disk.
2. **`pre-commit` hook stages the approval into the commit.** `.regin/`
   is `.gitignore`d by design, so the hook `git add -f`s `topic.json`
   to override the ignore; wiki `.md` files under `.regin/topics/wiki/`
   are tracked normally.
3. **User B pulls.** The new approved-graph files land on disk; B's
   local `GraphSnapshot` row is now stale.
4. **`post-merge` / `post-checkout` hook runs `regin topics import`.**
   The import reads disk, hashes it against the latest snapshot, and
   (on drift) inserts a new `GraphSnapshot` with `reason=git_pull`. B's
   next `regin route` or `/api/.../route` call sees the new state.

If B doesn't have the hooks installed, the next `load_authoritative_graph`
call (triggered by routing, scanning, or any approved-graph read) detects
the drift and auto-seeds a new snapshot with `reason=auto_seed`. The
hook just makes that work explicit and front-loaded.

## CLI surface

```bash
# Install the three hooks once per clone.
regin topics install-hook

# Manually sync (idempotent — no-op when in sync). Run this if you
# pulled without the hook installed.
regin topics import [--reason git_pull] [--quiet]

# Audit cross-machine drift across all registered repos.
regin doctor      # shows the "Topic graph sync (per repo)" group
```

The doctor's drift warning self-clears on the next approved-graph read
(routing, scanning, evidence build) because `load_authoritative_graph`
auto-seeds a snapshot when it detects disk-newer-than-snapshot. So a
⚠ row can flip to ✓ between consecutive `regin doctor` calls without
the user explicitly running `topics import`. The warning is still
useful: it tells you the local snapshot *was* stale at observation
time, which is what users need to know before doing anything that
caches snapshot content (e.g. a long-running `regin serve` process).

## Cross-state contract

The only state interaction the design introduces: an in-flight
`downgrade(X)` proposal carries its own copy of `X`'s payload inside
`ProposalRevisionTopic`. An upstream pull that re-introduces (or
modifies) `X` in `topic.json` updates the live graph but **must not**
mutate the proposal's stored copy. This is pinned by
`tests/topics/test_cli_topics_import.py::test_import_preserves_in_flight_downgrade_proposal`.

If the user later regenerates and applies that downgrade proposal, the
apply path goes through the normal `audit_graph` diff check so the
proposed change is validated against the *current* live graph — not
the graph at the time the proposal was first drafted.

## Known limitation: merge conflicts on `topic.json`

`topic.json` is one JSON file containing every approved topic. Two
users approving topics on parallel branches will see git merge
conflicts when they merge. The file is structured so conflicts are
resolvable, but at larger team sizes this gets annoying. The natural
future escape hatch — not built — is to split the graph into one file
per topic under `.regin/topics/topics/<topic_id>.json` so the merge
surface shrinks to "did someone else touch the same topic id".

## When you do want a shared database

Standalone-with-git-shared-state covers "everyone benefits from my
approvals" but doesn't extend to:

- Cross-user proposal review **in real time**. Async review is covered
  by proposal bundles (see "Sharing an in-flight proposal" above);
  live co-editing of the same draft is not.
- A live audit log of who approved what across the whole team.
- Concurrent editing of the same proposal.

These need the shared-server model: one `regin serve` instance, JWT
auth (see `lib/auth.py`), and a shared backend via
`REGIN_DATABASE_URL=mysql+pymysql://…`. That route is supported by the
codebase but documented separately under [setup](../setup.md).
