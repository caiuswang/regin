# Multi-user topic sharing

How one user's approved topics become visible to other users on the team, given regin's default standalone model (each user runs their own instance against their own local SQLite). The short answer: **git carries the approved graph; each user's local DB is a cache of what those files say.** No shared database is required.

For the single-user proposal lifecycle, see [[topic-proposal-pipeline]]. For how the approved graph is consumed at query time, see [[topic-routing]]. This topic is the bridge between them.

## What ships vs. what stays local

| Artifact | Path | Travels via git? |
| --- | --- | --- |
| Approved graph (single-file layout) | `.regin/topics/topic.json` | yes (force-added by pre-commit) |
| Approved graph (split layout) | `.regin/topics/topics/<id>.json` + `_meta.json` | yes (force-added by pre-commit) |
| Per-topic wiki bodies | `.regin/topics/wiki/<id>.md` | yes |
| Exported proposal bundles | `.regin/topics/bundles/*.json` | yes |
| Machine-local overlay | `.regin/topics/topic.local.json` | no (gitignored) |
| In-flight proposal runs, revisions, feedback | `proposal_runs` / `proposal_revisions` / `proposal_feedback_*` tables | no, local SQLite only |
| `GraphSnapshot` history | local SQLite | no, per-machine cache |
| Per-run artefact dirs | `.regin/topics/proposals/<id>/` | no (gitignored) |

`.gitignore` blanket-ignores `.regin/*` and then re-includes exactly the shareable slices: `topic.json`, `topics/*.json` + `topics/_meta.json`, `wiki/*.md`, and `bundles/*.json`. Everything else — including the overlay that holds locally-approved but not-yet-shared topics — stays on the machine that produced it.

The approved graph exists in one of two on-disk shapes. Reads are shape-agnostic: `load_graph` (lib/topics/core.py:173) prefers the split layout when `.regin/topics/topics/` holds `*.json` (`split_layout_active`, lib/topics/core.py:110) and otherwise falls back to the legacy single `topic.json`. Writes follow the shape already on disk via `write_graph_to_disk` (lib/topics/core.py:288). Both shapes travel via git identically; the split layout only shrinks the merge surface (see the last section).

## End-to-end flow: approval on A → visibility on B

```
   user A                        git                 user B
  ┌─────────────┐              ┌──────┐            ┌──────────────┐
  │ approve     │ pre-commit   │topic │  git pull  │ post-merge   │
  │  (apply)    │ ───────────► │.json │ ─────────► │ hook         │
  │   ↓         │  git add -f  │  +   │            │   ↓          │
  │ overlay     │              │wiki/ │            │ regin topics │
  │   ↓         │              │  +   │            │ import       │
  │ promote <id>│              │bundl.│            │   ↓          │
  │ → topic.json│              │      │            │ GraphSnapshot│
  │   ↓         │              │      │            │ (local cache)│
  │ snapshot DB │              │      │            └──────────────┘
  └─────────────┘              └──────┘
```

### Producer (user A)

1. **Approve / draft locally.** A proposal approval, scan, or downgrade writes the gitignored `topic.local.json` overlay. The merged view `load_graph_merged()` (lib/topics/core.py:244) layers this on top of the base graph, so the topic is live for A but invisible to anyone else.
2. **Promote.** `regin topics promote <id>` (`cmd_topics_promote` in cli/commands/topics.py:161; `promote_topic` in lib/topics/scan.py:346) moves the entry out of the overlay into the git-tracked base graph. Its wiki already lives under `.regin/topics/wiki/<id>.md`. The effective merged graph is unchanged for A; only the storage shifted. `regin topics promote --all` (`promote_all_topics`, lib/topics/scan.py:390) does the same for every pending overlay change in one pass.
3. **Commit.** `git commit` runs the regin pre-commit hook (`_PRE_COMMIT_BODY` in lib/topics/scan.py:529) which runs `topics check`, refreshes refs with `topics scan --staged`, and force-`git add`s the approved graph (`topic.json` and/or the `topics/` split dir), `wiki/`, and `bundles/` so they are staged despite the `.regin/*` ignore. The `topic.local.json` overlay is never staged.

### Consumer (user B)

1. **`git pull`.** The new graph files, wikis, and bundles land on disk. B's local `GraphSnapshot` row (per-machine SQLite cache) is now stale.
2. **`post-merge` / `post-checkout` hook runs `regin topics import`** (`_POST_MERGE_BODY` / `_POST_CHECKOUT_BODY` in lib/topics/scan.py:547-557). The import reads disk, hashes it against the latest snapshot, and on drift inserts a new `GraphSnapshot` with `reason=git_pull`.
3. **Visible everywhere.** B's next `regin topics route`, topic-router skill invocation, or WebUI topic view goes through `load_authoritative_graph` (lib/topics/graph_io.py:68) and now returns A's topic — no proposal/approval is re-run on B.

If B never installed the hooks, the next call to `load_authoritative_graph` still detects disk-newer-than-snapshot and auto-seeds a fresh snapshot with `reason=auto_seed` (`_auto_seed_snapshot`, lib/topics/graph_io.py:130). The hook just front-loads that work to pull time and makes drift visible to `regin doctor` ("Topic graph sync (per repo)" group).

## Setup (one-time, per clone)

```bash
regin topics install-hook        # installs pre-commit, post-commit, post-merge, post-checkout
regin topics import --quiet      # manual sync if you pulled without the hook installed
regin doctor                     # audit cross-machine drift across all registered repos
```

`install_topic_hooks` (lib/topics/scan.py:582) writes all four hooks atomically and overwrites any existing files at those paths. Beyond the sync trio, it also installs a `post-commit` hook that runs `regin topics drift` to follow file renames a commit introduced into topic refs (a no-op unless `mechanical_autoapply` is on).

## Cross-state contracts

- **In-flight proposals are immune to upstream pulls.** When B has a `downgrade(X)` proposal open and A's pull re-introduces X in the base graph, the live graph updates but `ProposalRevisionTopic` keeps the snapshot of X it was drafted against. Pinned by `tests/topics/test_cli_topics_import.py::test_import_preserves_in_flight_downgrade_proposal`. If B later applies that proposal, `audit_graph` re-validates against the *current* live graph, not the one at draft time.
- **Snapshot writes commit last.** In `apply_diff`, disk is written before the SQL commit; a crash between them leaves disk≠SQL, which `load_authoritative_graph` reconciles by re-seeding the snapshot from disk on the next read (lib/topics/graph_io.py:68).
- **Embedding index isn't shipped.** `topics import` populates the graph + wiki bodies but not the dense embedding index used for semantic search. Run `regin wiki index` for full search parity (apply paths refresh it automatically).

## Layout & merge surface

The legacy layout keeps all approved topics in one `topic.json`, so two users approving on parallel branches collide on that single file. `regin topics migrate-split` (`cmd_topics_migrate_split` in cli/commands/topics.py:996) converts a repo to one file per topic under `.regin/topics/topics/<id>.json` plus a `_meta.json` sidecar for top-level fields (`write_split_graph`, lib/topics/core.py:308), shrinking the merge surface to "did anyone else touch the same id". Wiki files are already per-topic, so they never had this problem. The migration is one-way and the split layout is only readable by regin versions that understand the split dir — teammates must upgrade to a dual-read regin *before* the migrating commit reaches them, which is why the command prints that warning.

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