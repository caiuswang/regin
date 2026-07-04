# Setup & Operations

Install, configure, and operate a regin instance. For what regin is and why, see the [README](../README.md). For internals, see [ARCHITECTURE.md](../ARCHITECTURE.md).

## Quick Start

```bash
git clone <repo-url> regin
cd regin
./scripts/setup.sh /path/to/your/source-repos
```

The setup script creates a Python venv, builds the frontend, initializes the database, discovers repos, and prompts you to create your user account. Then:

```bash
.venv/bin/python cli/regin.py serve
# Open http://localhost:8321 and log in
```

## Manual Setup

### 1. Install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cd frontend && npm install && npx vite build && cd ..
```

### 2. Initialize database

```bash
.venv/bin/python cli/regin.py init --force
.venv/bin/python cli/regin.py rebuild
```

`init --force` does a true local reset: it recreates the SQLite DB from scratch, removes tracked deployed skill directories, clears local experiments, tags, accounts, deployment records, and rotates the JWT secret so old login tokens stop working.

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
- `active_provider` selects the AI-agent integration backend (`claude`, `codex`, `generic`). Default is `claude`.

Repos are registered explicitly through the `/repos` page in the web UI, or from the CLI:

```bash
.venv/bin/python cli/regin.py add-repo /path/to/your/source-repo
```

Registered repo paths persist in `settings.repo_paths`. Use `regin remove-repo <name>` (or the Remove button on `/repos`) to unregister.

### 4. Initialize auth/audit tables and create your account

```bash
.venv/bin/python cli/regin.py users init-db                               # create tables (first time only)
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

## Shared vs local state

Each machine runs its own local instance. Shareable skills (the packaged, versioned kind) live in the optional `regin-skillhub` sibling repo — see [CLAUDE.md](../CLAUDE.md) and `pattern promote` for that flow. The layering of what persists where:

| Layer | What | Where |
|-------|------|-------|
| **Git (shared)** | team settings | `config/settings.json` |
| **XDG data dir (user-local)** | procedure guides, rule-engine sources + generated index, tag definitions | `$XDG_DATA_HOME/regin/patterns/`, `$XDG_DATA_HOME/regin/grit/` (when the grit engine is configured), `$XDG_DATA_HOME/regin/config/tags.yaml` (default base `~/.local/share/regin/`) |
| **Auth/audit** | user accounts, roles, audit log | MySQL when `mode: shared`; SQLite when `mode: standalone` |
| **SQLite (local)** | pattern index, repo tracking, experiments, rule triggers | `db/*.db` (rebuilt from on-disk files via `regin rebuild`) |
| **Local files** | machine-specific paths, JWT secret | `config/settings.local.json`, `config/jwt_secret.txt` |

**Patterns, rule-engine sources, and tag definitions are user-generated data** and live in your XDG data directory, not the repo. Override locations with env vars `REGIN_PATTERNS_DIR`, `REGIN_GRIT_DIR`, `REGIN_TAGS_PATH`, or settings `patterns_dir`, `grit_dir`, `tags_path` (local-only) in `config/settings.local.json`. Use `REGIN_DATA_DIR` to relocate the whole regin data tree at once.

## Agent provider architecture

regin routes skills/hooks/session path conventions through provider adapters under `lib/providers/` instead of hard-coding Claude paths across modules.

- `claude` is the default and fully supported.
- `codex` and `generic` are scaffolded adapters (capability-gated stubs).
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

```bash
regin init                              # initialize DB + directories
regin doctor                            # check environment and missing CLI tools
regin discover                          # register sibling source repos
regin add-repo /path/to/repo            # register one repo by path
regin remove-repo <name>                # unregister a repo
regin search <keyword>                  # search patterns
regin tags --index                      # generate tag/repo indexes
regin rebuild                           # rebuild DB from git-tracked files
regin serve                             # start web dashboard on :8321
```

Grouped subcommands (run any of them with `--help` for the full list):

```bash
regin users    <init-db|list|create|reset-password|set-role|delete>
regin skills   <list|check|pull|push|undeploy>
regin pattern  <promote|import|import-dir|embed|route|enable-rules|rules-doctor>
regin rules    <check|index|deploy|list-disabled|disable|enable|run|list>
regin topics   <bootstrap|scan|check|import|install-hook|router-skill|wiki|route|audit|audit-fix>
regin trace    <backfill-tokens|resolve-repos|backfill-costs>
```

## Troubleshooting

**"Database not initialized"** — Run `regin init` then `regin rebuild`.

**Login not working after DB rebuild** — User accounts are local. Re-create: `regin users create <name> <pass> --role admin`

**Forgot admin password** — Reset from CLI: `regin users reset-password <name> <newpass>`

**Port in use** — `regin serve --port 8322`
