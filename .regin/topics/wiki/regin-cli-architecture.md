## regin CLI architecture & command surface

### Entrypoint (`cli/regin.py`)
A deliberately thin shim. It does two things before handing off to `cli.app`:
1. **Re-execs under the project venv** — if `sys.executable` isn't `<root>/.venv/bin/python` and that interpreter exists, it `os.execv`s into it with the original `argv`. This is why `python cli/regin.py …` works right after setup even though the system interpreter lacks the deps (matching the CLAUDE.md rule to use the `.venv` interpreter).
2. **Fixes `sys.path`** — when invoked as `python cli/regin.py`, `sys.path[0]` is `cli/`, so the project root is prepended to resolve `cli.app` without an editable install.

### Typer root (`cli/app.py`)
The root `typer.Typer(name="regin", no_args_is_help=True, pretty_exceptions_enable=False, …)` app is assembled here, and the module stays small on purpose — command bodies live next to their domain under `cli/commands/`. Two wiring conventions:

- **Flat top-level commands** — the module exposes `register(app)`, e.g. `db.register(app)` (`init`, `rebuild`, `tags`, `search`), `meta.register(app)` (`doctor`), `repo.register(app)`, `route.register(app)`, `goal.register(app)`, `gate.register(app)`, `session.register(app)`, `server.register(app)`.
- **Grouped subcommands** — the module exposes a pre-built sub-Typer added via `app.add_typer(...)`, giving `regin <group> <cmd>` forms (e.g. `users`, `skills`, `topics`, `patterns` via `pattern_app`, `rules`, `trace`, `wiki`, `logs`, `memory`, `grader` via `grade_app`). `schema.register(app)` and `messages.register(app)` wire their own commands directly.

A `@app.callback()` root (`_root`) runs before **every** command: it calls `configure_logging()` and `configure_activity_log()` once, then `_stamp_cli_invocation` logs a `command_invoked` event and arranges a `ctx.call_on_close` finalizer (`_finalize`) that records `command_completed` or, on error, `command_failed` with a `duration_ms`. Help/completion invocations (`ctx.invoked_subcommand is None`) are skipped, and a clean `SystemExit(code in (None, 0))` — e.g. from `--help` or `typer.Exit(0)` — is classified as a clean exit by `_is_clean_exit`, not a failure. Every real CLI run is therefore traceable in the activity log under `feature=cli`.

### Shared helpers
- **`cli/deps.py`** — the `@require_db` decorator centralizes the "is the DB initialised?" guard: if `db_exists()` is false it prints `Database not initialized. Run 'init' first.` and `sys.exit(1)`. It inspects nothing about the call signature (works with both Typer keyword handlers and argparse-style ones) and forwards every argument untouched.
- **`cli/output.py`** — `echo` / `error` / `table` write to module-level `_stdout` / `_stderr` sinks so tests can monkeypatch a `StringIO`. `table` is a thin whitespace-aligned renderer (column widths floored at `min_col_width`, an optional header underline) — no heavyweight dependency; reach for Rich directly if you need more. CLI output is UX; anything diagnostic should flow through structured logging (`lib.logging_setup.get_logger()`) instead.

### Environment health: `regin doctor`
`cmd_doctor` (`cli/commands/meta.py` → `lib/doctor.py:run_checks()`) is the environment health check. It walks grouped tool/hook checks plus a project-readiness group, printing `✓` / `✗` / `⚠` rows: present items show a version or path, missing required items show `✗` with an optional `→ Install:` hint, and missing optional items show `⚠ … missing (optional)`.

### Database lifecycle: `regin init` / `regin rebuild`
The SQLite DB is a **derived cache** whose authoritative inputs are git-tracked files. Two commands manage it, both in `cli/commands/db.py`.

**`regin init`** (`cmd_init`) creates the DB and directories. If the DB already exists it is a no-op unless `--force` is passed. On a normal run it calls `init_db()`, seeds builtin prompt-template skeletons via `seed_builtin_skeletons()` (bodies live in the Python registry, not `schema.sql`), and ensures `patterns_dir/_index/` exists.

`init --force` first runs `_force_reset_state()`, which tears down persisted local state before reinitializing:
- **`_clear_recorded_deployments()`** reads `deployed_path` rows from the `pattern_deployments` table (via a defensive `sqlite3` read that tolerates a missing table), deletes each deployed skill directory (`_remove_path`), and prunes now-empty parent dirs like `.claude/skills/` (`_prune_empty_parents`).
- **`_remove_primary_db()`** disposes pooled engine handles (`dispose_engine()`) then deletes the SQLite file and its `-wal` / `-shm` / `-journal` sidecars.
- In **shared mode** (`settings.mode == "shared"`), `_reset_shared_auth_tables()` drops and recreates the MySQL `users` and `audit_log` tables via `init_mysql()`.
- **`_clear_jwt_secret()`** removes the cached JWT signing secret (`lib.auth._SECRET_PATH`), invalidating existing login tokens.
- **`_clear_hook_manager_state()`** deletes the hook_manager toggle config and strips `hook_manager` dispatcher commands (matched by `-m hook_manager`) out of the provider's `settings.json` (`_clear_hook_manager_settings`), rewriting the file and dropping now-empty hook events. Provider paths come from `get_active_provider()`.

Each reset action is reported back to the user (deployed dirs removed, JWT secret cleared, hook toggle state cleared, routing entries removed) with correct singular/plural wording.

**`regin rebuild`** (`cmd_rebuild` → `lib/db_rebuild.py:rebuild_from_files(preserve_local=not clean)`) reconstructs the shared tables from files. By default it preserves the local-only tables; `--clean` drops them too. `_render_rebuild_stats` / `_render_repo_stats` echo the returned stats dict.

### How a rebuild reconstructs the cache (`lib/db_rebuild.py`)
`rebuild_from_files` opens one connection and runs a fixed sequence:
1. **Back up local-only tables** (when `preserve_local`) — `_backup_local_tables` snapshots rows from `_LOCAL_TABLES = {experiments, rule_triggers, users, audit_log, pattern_deployments}`, so runtime data (including recorded skill deployments and, in standalone mode, users/audit) survives the schema reset.
2. **Recreate the schema** — `_recreate_schema` turns off foreign keys, drops every non-`sqlite_sequence` table, then replays `load_schema_sql()` (i.e. `db/schema.sql`) via `executescript`.
3. **Restore local tables** — `_restore_local_tables` re-inserts the backed-up rows with `INSERT OR IGNORE`, silently skipping any row that violates the fresh schema.
4. **Seed tags** — `_seed_tags` populates `tags` from `config/tags.yaml` (category → name lists); missing file leaves tags empty (`tags_seeded` stays `None`).
5. **Seed prompt skeletons** — `_seed_prompt_skeletons` re-runs `seed_builtin_skeletons()` (idempotent by slug).
6. **Rebuild patterns** — `_rebuild_patterns` scans `patterns/*/SKILL.md`, skips `_`-prefixed slugs and files without YAML frontmatter (recorded in `patterns_skipped`), and inserts each into `pattern_docs` with a title, a repo-relative `file_path`, `category='procedure'`, and a SHA-256 `content_hash`.
7. **Rediscover repos** — `_rediscover_repos` calls `scan_repos()` + `register_repos()` from `lib.sync.repo_discovery` to repopulate `repos` / `branches`, capturing an `error` field instead of raising if discovery fails.

Every step also writes progress to the activity log under `feature=rebuild`. Note the schema-drift gotcha from CLAUDE.md: both `init` and `rebuild` build from `db/schema.sql`, **not** Alembic — any migration that adds tables/columns must also be folded into `db/schema.sql` or fresh installs diverge.

### Add-a-command playbook
1. Create `cli/commands/<domain>.py` exposing either a `register(app)` (flat commands) or a `<domain>_app = typer.Typer(...)` (grouped `regin <domain> <cmd>`).
2. Import it in `cli/app.py` and wire it (`<domain>.register(app)` or `app.add_typer(<domain>_app)`).
3. Gate DB-dependent handlers with `@require_db`; emit user-facing output via `cli/output.py`, and route diagnostics/history through the activity log (`get_activity_logger`).

---