# Setup & Operations

Install, configure, and operate a regin instance. For what regin is and why, see the [README](../README.md). For internals, see [ARCHITECTURE.md](../ARCHITECTURE.md).

## Quick Start

```bash
git clone <repo-url> regin
cd regin
./scripts/setup.sh /path/to/your/source-repos
```

The setup script needs Python ≥ 3.11 (pick one with `PYTHON=/path/to/python3.12 ./scripts/setup.sh`) and Node/npm. It creates a Python venv, links `regin` into `~/.local/bin` (`REGIN_BIN_DIR` to change), builds the frontend, creates or migrates the database, discovers repos, offers to wire regin's hooks into Claude Code, and prompts you to create your user account. Then:

```bash
.venv/bin/python cli/regin.py serve
# Open http://localhost:8321 and log in
```

Re-running `./scripts/setup.sh` is safe — it is also how you [upgrade](#upgrade).

## Manual Setup

### 1. Install dependencies

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[agent-sdk]'
cd frontend && npm ci && npx vite build && cd ..
```

### 2. Initialize database

```bash
.venv/bin/python cli/regin.py init       # builds db/regin.db at the current schema; skips an existing DB
.venv/bin/python cli/regin.py migrate    # brings an existing DB up to the current schema
```

`init --force` is a reset, not a setup step: it recreates the SQLite DB from scratch — every session, feature, task and account is gone — removes tracked deployed skill directories, and rotates the JWT secret so old login tokens stop working. Back up `db/` before using it.

### 3. Configure local settings

Create `config/settings.local.json`:

```json
{
  "mode": "standalone",
  "active_provider": "claude"
}
```

#### Server mode

Set `mode` in `config/settings.local.json` to choose how auth/audit data is stored:

**Standalone mode** (default — single user, no MySQL needed):
```json
{
  "mode": "standalone"
}
```

**Shared mode** (team mode with MySQL for user accounts/audit):
```json
{
  "mode": "shared",
  "database_url": "mysql://user:password@host:3306/regin"
}
```

- `mode` can be `shared` or `standalone`. Default is `standalone`.
- `database_url` is only needed when `mode` is `shared`.
- `active_provider` selects the AI-agent integration backend (`claude`, `codex`, `generic`, `kimi`). Default is `claude`.

Repos are registered explicitly through the `/repos` page in the web UI, or from the CLI:

```bash
.venv/bin/python cli/regin.py add-repo /path/to/your/source-repo
```

Registered repo paths persist in `settings.repo_paths`. Use `regin remove-repo <name>` (or the Remove button on `/repos`) to unregister.

### 4. Wire the agent hooks

```bash
.venv/bin/python cli/regin.py hooks install --provider claude   # writes ~/.claude/settings.json
.venv/bin/python cli/regin.py hooks status                      # installed vs expected, per provider
```

Without this step regin records no sessions and no rule engine runs. `--provider` also takes `codex` and `kimi`.

### 5. Create your account

```bash
.venv/bin/python cli/regin.py users init-db                               # shared mode only: create the MySQL tables
.venv/bin/python cli/regin.py users create <username> <password> --display-name "Your Name"
.venv/bin/python cli/regin.py serve
```

The first user gets the `admin` role automatically. In `shared` mode, all team members share the same MySQL user database. In `standalone` mode, accounts are stored locally in SQLite.

> ⚠️ **Don't expose `regin serve` to the network until the first user exists.** The dashboard binds to `127.0.0.1` by default — keep it that way until you've run `users create`. If you bind to `0.0.0.0` (or otherwise expose the port) before any account exists, anyone who can reach the URL can register and inherit the `admin` role. There is no separate "open registration" toggle.

### Switching modes

Changing `mode` does **not** delete any data.

- **Patterns, repos, tags, experiments, and rule triggers** always live in the local SQLite database (`db/regin.db`) and are preserved regardless of mode.
- **User accounts and audit logs** live in a different database depending on the mode:
  - `shared` → stored in **MySQL**.
  - `standalone` → stored in the same **SQLite** file.

When you switch modes, the app simply looks at a different database for auth/audit data, so you may need to re-create your admin user in the new mode. The old accounts remain in the previous database.

## Upgrade

```bash
git pull && ./scripts/setup.sh
```

Re-running setup is the upgrade path. It installs new Python and npm dependencies, rebuilds the frontend, runs `regin migrate` to bring `db/regin.db` to the new schema, and runs `regin hooks repair` when the hooks are already wired. Your database, `config/settings.local.json`, users and JWT secret are kept.

Upgrading by hand is the same steps: `pip install -e '.[agent-sdk]'`, `npm ci && npx vite build`, `regin migrate`, `regin hooks repair`.

`regin migrate` only upgrades a DB that carries an Alembic stamp, which every DB built by `regin init` does. A DB without one is stamped when it already has every table and column of `db/schema.sql`; otherwise `migrate` changes nothing, lists what is missing and exits non-zero. Such a DB predates v0.3.0 and has no upgrade path: back up `db/`, then `regin init --force`.

## Uninstall

```bash
regin uninstall --dry-run      # list what would be removed
regin uninstall                # remove it, after a confirmation prompt
```

`regin uninstall` removes what this checkout wrote outside the repo: its hook entries in each provider's settings (Claude, Codex, the Kimi managed block), the debug hooks, the deployed skill directories it has a deployment record for, each provider's `hook-manager-config.json`, and the `regin` symlink in `~/.local/bin` (`--bin-dir` or `REGIN_BIN_DIR` if setup linked it elsewhere). `--purge-data` also deletes `settings.data_dir` and `db/*.db`; without it your data stays.

Skill directories, hook-manager config and data are shared between checkouts, so they are kept — and the listing names who still uses them — while another checkout's hooks are still wired, while a provider's hook config can't be parsed, or when you run it from a git worktree rather than the main checkout. Hook entries written by another checkout are never touched.

It does not delete the checkout or anything you installed by hand. Finish with the steps it prints: `/plugin uninstall …` for the Claude Code plugins, remove any `statusLine` entry pointing into the checkout, then `rm -rf <checkout>`.

## Shared vs local state

Each machine runs its own local instance. Shareable skills (the packaged, versioned kind) live in the optional `regin-skillhub` sibling repo — see [CLAUDE.md](../CLAUDE.md) and `pattern promote` for that flow. The layering of what persists where:

| Layer | What | Where |
|-------|------|-------|
| **Git (shared)** | team settings | `config/settings.json` |
| **XDG data dir (user-local)** | procedure guides, rule-engine sources + generated index, tag definitions | `$XDG_DATA_HOME/regin/patterns/`, `$XDG_DATA_HOME/regin/grit/` (when the grit engine is configured), `$XDG_DATA_HOME/regin/config/tags.yaml` (default base `~/.local/share/regin/`) |
| **Auth/audit** | user accounts, roles, audit log | MySQL when `mode: shared`; SQLite when `mode: standalone` |
| **SQLite (local)** | sessions, features, tasks, experiments, rule triggers, plus a pattern / tag / repo cache | `db/*.db` (`regin rebuild` re-derives only the cache tables) |
| **Local files** | machine-specific paths, JWT secret | `config/settings.local.json`, `config/jwt_secret.txt` |

**Patterns, rule-engine sources, and tag definitions are user-generated data** and live in your XDG data directory, not the repo. Override locations with env vars `REGIN_PATTERNS_DIR`, `REGIN_GRIT_DIR`, `REGIN_TAGS_PATH`, or settings `patterns_dir`, `grit_dir`, `tags_path` (local-only) in `config/settings.local.json`. Use `REGIN_DATA_DIR` to relocate the whole regin data tree at once.

## Agent provider architecture

regin routes skills/hooks/session path conventions through provider adapters under `lib/providers/` instead of hard-coding Claude paths across modules.

- `claude` is the default and fully supported.
- `codex` and `kimi` are functional adapters (skills/hooks/sessions/transcript capabilities enabled); `generic` is a capability-gated stub.
- Provider capabilities are exposed via `GET /api/providers` and included in `regin doctor`.

Provider-specific path overrides can be configured in `settings.local.json`:

```json
{
  "active_provider": "claude",
  "providers": {
    "claude": {
      "skills_dir": "~/.claude/skills",
      "hook_settings_path": "~/.claude/settings.json",
      "transcript_projects_dir": "~/.claude/projects"
    }
  }
}
```

## Run a task with Kimi

A task can be armed on Kimi Code instead of Claude.

1. **Install and sign in.** `kimi` on `PATH` (`regin doctor` lists it under *Task launch providers*), then `kimi login` once.
2. **Arm.** In the task's arm panel pick **Kimi Code** in the provider picker; it is greyed out, naming the missing binary, when `kimi` isn't installed. Only the fields Kimi declares are shown (a model id, free text) — no permission mode or effort.
3. **Pick a launch.**
   - **ACP** — regin drives `kimi acp` as a child of `regin serve`. Stopping or restarting the server ends the run.
   - **tmux** — `kimi` runs in a tmux session on regin's own tmux server, so a `regin serve` restart does not interrupt it.
4. **Watch it.** The run shows on `/live` like any session, with steer and Stop. To look at the pane itself, attach to regin's tmux server (socket `regin-bridge` by default, `settings.agent_bridge.attach_socket`): `tmux -L regin-bridge attach -t task-<task id>-a<attempt>`. Detach with `Ctrl-b d`; closing your client never ends the session.

Known limits:

- Runs are unattended: panes start with `--yolo`, and an ACP run answers approval and question requests with `cancelled`.
- A steer into a running turn is cancel-then-re-prompt; the ACP protocol exposes no mid-turn injection.
- `codex` and `generic` cannot be armed — arming them is refused rather than run as Claude.
- `/live` can launch and Resume Kimi sessions over ACP, but its *terminal* session type still opens only Claude.
- A new worktree needs Kimi's workspace trust; regin writes the record before launch, so no Trust dialog should appear.

Design and the per-provider declaration: *Provider launch capabilities* in [ARCHITECTURE.md](../ARCHITECTURE.md#provider-launch-capabilities-task-runs-on-a-non-claude-agent).

## Topic proposals

regin can generate reviewable topic graph drafts by running a configured external tool-using agent (e.g. Claude Code). Proposal runs reuse the session trace viewer for monitoring and write drafts under `.regin/topics/proposals/<run_id>/` until a user explicitly accepts or merges them.

See [docs/topics/proposals.md](topics/proposals.md) for setup, CLI/WebUI usage, output contract, safety rules, and architecture.

## User roles

| Role | View | Edit patterns/skills/rules/settings | Manage users |
|------|------|-------------------------------------|--------------|
| **admin** | all | all | yes |
| **editor** | all | all | no |
| **viewer** | all | own profile only | no |

### User management

```bash
regin users list                                      # list all users
regin users create alice pass123 --role editor        # create user
regin users reset-password alice newpass               # reset password (no old password needed)
regin users set-role alice admin                      # change role
regin users delete alice                              # delete user
```

Admins can also manage roles from the web UI at `/account`.

## Agent bridge (steer live sessions from /live)

Off by default. When enabled, the `/live` session page renders a composer that
sends a prompt into an idle Claude Code session — or a steering message into a
running one — via guarded tmux keystroke injection. Design and security model:
[agent-bridge-design.md](./agent-bridge-design.md).

All four conditions must hold before the composer appears:

1. **Enable the feature** — in the gitignored `config/settings.local.json`
   (`scripts/setup.sh` offers this when creating the file):

   ```json
   "agent_bridge": { "enabled": true }
   ```

   The `token` field is only for headless callers of `/api/bridge/*`; the web
   composer authenticates with your normal login and never needs it.

2. **Export the opt-in env var in the shell that launches claude.** Pane
   registration is per-session consent: the SessionStart hook reads
   `REGIN_BRIDGE` from the claude process's environment at launch, so a
   one-off prefix in another terminal does nothing. Set it at profile level:

   ```fish
   set -Ux REGIN_BRIDGE 1         # fish (universal, survives restarts)
   ```

   ```bash
   export REGIN_BRIDGE=1          # zsh/bash — add to your profile
   ```

3. **Run claude inside tmux.** Delivery is tmux `send-keys`; a session
   launched outside tmux never registers a pane.

4. **Log in with an editor-role account.** Sending is gated `require_editor`
   (see [User roles](#user-roles)); viewers see the page but cannot send.

`regin doctor` has an *Agent bridge* group that checks each condition and
reports how many sessions are currently reachable.

## CLI reference

<!-- cli-reference:start (generated by scripts/gen_cli_reference.py) -->

Run any command with `--help` for its arguments and flags.

| Command | What it does |
|---|---|
| `regin init` | Initialize database and directories |
| `regin rebuild` | Re-derive the pattern, tag and repo cache tables from files; other tables are untouched |
| `regin migrate` | Reconcile an existing DB with the current code's schema |
| `regin tags` | Manage tags |
| `regin search` | Search patterns |
| `regin doctor` | Check environment health and missing CLI tools |
| `regin discover` | Re-register every repo in settings.repo_paths |
| `regin add-repo` | Register a single git repo by path |
| `regin remove-repo` | Unregister a repo by name |
| `regin prune-repos` | Drop Repo rows whose path is missing or under $TMPDIR |
| `regin route` | EXPERIMENTAL. Route a query through the dense pattern index |
| `regin session-id` | Print the current agent session id (from $REGIN_SESSION_ID or the provider's own variable, e.g. $CLAUDE_CODE_SESSION_ID). |
| `regin serve` | Start web dashboard |
| `regin uninstall` | Remove regin's hooks, deployed skills, hook-manager config and launcher symlink written by this checkout; --purge-data also deletes its data. |
| `regin bootstrap-hook-schemas` | Infer hook-event JSON Schemas from the hook-payload JSONL log |

Command groups — `regin <group> <subcommand>`:

| Group | What it covers | Subcommands |
|---|---|---|
| `regin goal` | Loop-engineering helpers (roadmap preflight for /goal-verified) | `preflight` · `feedback` |
| `regin gate` | Trace-derived pass/fail gates for unenforced skill steps | `recall-ran` · `task-recall-ran` · `pipeline-ran` |
| `regin session` | What regin holds about one agent session | `context` |
| `regin users` | Manage user accounts | `init-db` · `list` · `create` · `reset-password` · `set-role` · `delete` |
| `regin skills` | Manage the active provider's skills directory entries | `list` · `check` · `pull` · `push` · `undeploy` |
| `regin topics` | Maintain repo-local curated topic graphs | `bootstrap` · `scan` · `list` · `promote` · `delete` · `drift` · `evolve` · `drift-dismiss` · `drift-note` · `cascade-stale` · `digest-refs` · `review-note` · `review-finish` · `proposal-finish` · `proposal-reap` · `propose` · `proposal-list` · `proposal-show` · `proposal-wiki` · `proposal-diff` · `proposal-apply` · `proposal-review-state` · `proposal-feedback` · `proposal-export` · `proposal-import` · `check` · `reconcile` · `import` · `install-hook` · `router-skill` · `rebuild-query-df` · `wiki` · `wiki-debt` · `wiki-stats` · `backfill-tiers` · `backfill-topic-wiki` · `route` · `audit` · `audit-fix` · `split-leaf` · `group` |
| `regin pattern` | Manage patterns (promote to regin-skillhub) | `promote` · `import` · `import-dir` · `embed` · `route` · `enable-rules` · `rules-doctor` |
| `regin rules` | Manage rule engines and targeted checks | `check` · `index` · `deploy` · `list-disabled` · `disable` · `enable` · `run` · `list` · `bundles` · `trust` · `untrust` |
| `regin schema` | Manage PostToolUse payload schemas | `list` · `bootstrap` · `validate` · `diff` |
| `regin trace` | Session trace maintenance | `dump` · `span` · `backfill-tokens` · `backfill-skill-tokens` · `backfill-model` · `resolve-repos` · `ingest-workflows` · `backfill-costs` · `reap-pending` · `prune` · `backfill-subagents` |
| `regin wiki` | Manage the wiki-page dense index (per-topic accepted wikis) | `index` |
| `regin logs` | View and manage the regin activity log | `list` · `tail` · `grep` · `prune` · `path` |
| `regin memory` | Cross-session agent memory | `recall` · `read` · `index-root` · `index-expand` · `index-fetch` · `recall-for-task` · `list` · `stats` · `remember` · `backfill-titles` · `retitle` · `export-tree` · `import-tree` · `topic-feedback` · `topic-decide` · `exemplar-add` · `exemplar-rm` · `exemplar-list` · `exemplar-forget` · `reflect` · `distill` · `link-topics` · `consolidate-skills` · `approve` · `forget` · `restore` · `supersede` · `eval` · `curate-apply` |
| `regin messages` | Inspect and prune the send_to_user inbox (agent_messages) | `list` · `read` · `stats` · `prune` |
| `regin bridge` | Agent bridge: a session's Mod channel | `listen` · `hold` · `release` |
| `regin events` | Inspect the declared notification event bus (agent_messages). | `list` |
| `regin grade` | Post-hoc session rubric grades | `run` · `show` · `list` · `reflect` · `pareto` |
| `regin hooks` | Inspect, install, and repair regin's agent hook wiring. | `status` · `install` · `remove` · `adopt` · `repair` |
| `regin stats` | Engineering-time analytics over real (non-test) sessions | `time` · `sessions` · `daily` · `session` · `by` |
| `regin defects` | The defect ledger: what a feature got wrong, and what escaped | `open` · `list` · `rounds` · `attribute` · `close` · `reopen` · `edit` · `merge` · `fix-item` · `image` |
| `regin feature` | Where a feature stands in the pipeline, and what it cost | `stage` · `attribute` · `unattribute` · `ledger` · `new` · `link` · `unlink` · `spec` · `image` · `ship` · `reopen` · `abandon` · `refresh` |
| `regin task` | Capture and inspect task ideas (agent_tasks) | `add` · `list` · `show` · `delete` · `comment` · `finish` · `arm` · `commands` · `slice-check` · `transcribe` · `tail` |
| `regin usage` | Act on a session's usage measurements (rate-limit alerts) | `measure` |

<!-- cli-reference:end -->

Regenerate this section after adding or renaming a command: `.venv/bin/python scripts/gen_cli_reference.py`.

## Troubleshooting

**"Database not initialized"** — Run `regin init`.

**Pages 500 after `git pull`** — The schema moved on and the DB is behind; `regin doctor` says so. Run `regin migrate` (or re-run `./scripts/setup.sh`). Don't reach for `regin rebuild`: it only re-derives the pattern, tag and repo cache tables and never changes the schema.

**`regin migrate` refuses, listing missing tables or columns** — See [Upgrade](#upgrade): back up `db/`, then `regin init --force`.

**Sessions don't show up in `/trace`** — The hooks aren't wired, or point at a moved checkout. `regin hooks status` shows what is installed; `regin hooks repair` rewrites drifted entries and names `regin hooks adopt` when the entries belong to a checkout that has since moved.

**Login not working after `regin init --force`** — User accounts are local. Re-create: `regin users create <name> <pass> --role admin`

**Forgot admin password** — Reset from CLI: `regin users reset-password <name> <newpass>`

**Port in use** — `regin serve --port 8322`
