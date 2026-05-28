# Multi-user topic sharing

How one user's approved topics become visible to other users on the team, given regin's default standalone model (each user runs their own instance against their own local SQLite). The short answer: **git carries the approved graph; each user's local DB is a cache of what those files say.** No shared database is required.

For the single-user proposal lifecycle, see [[topic-proposal-pipeline]]. For how the approved graph is consumed at query time, see [[topic-routing]]. This topic is the bridge between them.

## What ships vs. what stays local

| Artifact | Path | Travels via git? |
| --- | --- | --- |
| Approved graph | `.regin/topics/topic.json` | yes (force-added by pre-commit) |
| Per-topic wiki bodies | `.regin/topics/wiki/<id>.md` | yes (tracked normally) |
| Machine-local overlay | `.regin/topics/topic.local.json` | no (gitignored) |
| In-flight proposal runs, revisions, feedback | `proposal_runs` / `proposal_revisions` / `proposal_feedback_*` tables | no, local SQLite only |
| `GraphSnapshot` history | local SQLite | no, per-machine cache |
| Per-run artefact dirs | `.regin/topics/proposals/<id>/` | no (gitignored) |

`.gitignore` blanket-ignores `.regin/` and then re-includes exactly `topic.json` and `wiki/*.md` (`.gitignore` lines 13-22). Everything else — including the overlay that holds locally-approved but not-yet-shared topics — stays on the machine that produced it.

## End-to-end flow: approval on A → visibility on B

```
   user A                        git                 user B
  ┌─────────────┐              ┌──────┐            ┌──────────────┐
  │ approve     │ pre-commit   │topic │  git pull  │ post-merge   │
  │  (apply)    │ ───────────► │.json │ ─────────► │ hook         │
  │   ↓         │  git add -f  │  +   │            │   ↓          │
  │ overlay     │              │wiki/ │            │ regin topics │
  │   ↓         │              │      │            │ import       │
  │ promote <id>│              │      │            │   ↓          │
  │ → topic.json│              │      │            │ GraphSnapshot│
  │   ↓         │              │      │            │ (local cache)│
  │ snapshot DB │              │      │            └──────────────┘
  └─────────────┘              └──────┘
```

### Producer (user A)

1. **Approve / draft locally.** A proposal approval, scan, or downgrade writes the gitignored `topic.local.json` overlay. The merged view `load_graph_merged()` (lib/topics/core.py:186) layers this on top of base `topic.json`, so the topic is live for A but invisible to anyone else.
2. **Promote.** `regin topics promote <id>` (`cmd_topics_promote` in cli/commands/topics.py; `promote_topic` in lib/topics/scan.py:331) moves the entry from the overlay into the git-tracked `topic.json` (and writes its wiki under `.regin/topics/wiki/<id>.md`). The effective merged graph is unchanged for A; only the storage shifted.
3. **Commit.** `git commit` runs the regin pre-commit hook (`_PRE_COMMIT_BODY` in lib/topics/scan.py:473) which runs `topics check`, refreshes refs with `topics scan --staged`, and `git add`s `.regin/topics/topic.json` and `.regin/topics/wiki/` so they are forced into the commit despite the `.regin/*` ignore.

### Consumer (user B)

1. **`git pull`.** The new `topic.json` and wiki `.md` files land on disk. B's local `GraphSnapshot` row (per-machine SQLite cache) is now stale.
2. **`post-merge` / `post-checkout` hook runs `regin topics import`** (`_POST_MERGE_BODY` / `_POST_CHECKOUT_BODY` in lib/topics/scan.py:486-495). The import reads disk, hashes it against the latest snapshot, and on drift inserts a new `GraphSnapshot` with `reason=git_pull`.
3. **Visible everywhere.** B's next `regin topics route`, topic-router skill invocation, or WebUI topic view goes through `load_authoritative_graph` (lib/topics/graph_io.py:60) and now returns A's topic — no proposal/approval is re-run on B.

If B never installed the hooks, the next call to `load_authoritative_graph` still detects disk-newer-than-snapshot and auto-seeds a fresh snapshot with `reason=auto_seed`. The hook just front-loads that work to pull time and makes drift visible to `regin doctor` ("Topic graph sync (per repo)" group).

## Setup (one-time, per clone)

```bash
regin topics install-hook        # installs pre-commit, post-merge, post-checkout
regin topics import --quiet      # manual sync if you pulled without the hook installed
regin doctor                     # audit cross-machine drift across all registered repos
```

`install_topic_hooks` (lib/topics/scan.py:513) writes all three hooks atomically and overwrites any existing files at those paths.

## Cross-state contracts

- **In-flight proposals are immune to upstream pulls.** When B has a `downgrade(X)` proposal open and A's pull re-introduces X in `topic.json`, the live graph updates but `ProposalRevisionTopic` keeps the snapshot of X it was drafted against. Pinned by `tests/topics/test_cli_topics_import.py::test_import_preserves_in_flight_downgrade_proposal`. If B later applies that proposal, `audit_graph` re-validates against the *current* live graph, not the one at draft time.
- **Snapshot writes commit last.** In `apply_diff`, disk is written before the SQL commit; a crash between them leaves disk≠SQL, which `load_authoritative_graph` reconciles by re-seeding the snapshot from disk on the next read (lib/topics/graph_io.py:60-95).
- **Embedding index isn't shipped.** `topics import` populates the graph + wiki bodies but not the dense embedding index used for semantic search. Run `regin wiki index` for full search parity (apply paths refresh it automatically).

## Known limitation: `topic.json` is one file

All approved topics share one JSON file, so two users approving on parallel branches see a merge conflict on `topic.json`. The structure is conflict-resolvable but gets annoying at larger team sizes. The not-yet-built escape hatch is to split into one file per topic under `.regin/topics/topics/<id>.json`, shrinking the merge surface to "did anyone else touch the same id". Wiki files are already per-topic so they don't have this problem.

## When standalone-with-git isn't enough

The sharing model above covers **approved knowledge propagation**. It does not cover:

- Cross-user review of an *in-flight* proposal (B looking at A's mid-draft work, leaving comments).
- A team-wide audit log of who approved what.
- Concurrent editing of the same proposal run.

These require the shared-server model: one `regin serve` instance behind JWT auth (`lib/auth.py`, `@require_auth` / `@require_editor` / `@require_role`) and a shared backend via `REGIN_DATABASE_URL=mysql+pymysql://…`. The codebase supports this — see `docs/setup.md` — but the proposal/review tables (`ProposalRun`, `ProposalRevision`, `ProposalFeedbackThread`) are then physically shared, not just their git-exported approval outputs.

## See also

- [[topic-proposal-pipeline]] — how a topic becomes approved on one machine in the first place.
- [[topic-routing]] — how the approved graph is consumed at query time once it has landed on a machine.
- [[proposal-review-comments]] — review comments are intentionally **not** shared via git; they live only in the producing user's SQLite unless a shared server is configured.