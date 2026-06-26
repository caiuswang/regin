# CLI & ops

Two proposed topics give the `ops-cli` bucket its first detailed children: the **CLI command surface** (how `regin <cmd>` is wired and extended) and **ops observability** (how regin records and inspects its *own* operations, as opposed to agent session traces).

---

## 1. regin CLI architecture & command surface

### Entrypoint (`cli/regin.py`)
A deliberately thin shim. It:
1. **Re-execs under the project venv** — if `sys.executable` isn't `.venv/bin/python` and that interpreter exists, it `os.execv`s into it. This is why `python cli/regin.py …` works right after setup even though the system interpreter lacks the deps (matching the CLAUDE.md rule to use the `.venv` interpreter).
2. **Fixes `sys.path`** — when invoked as `python cli/regin.py`, `sys.path[0]` is `cli/`, so the project root is prepended to resolve `cli.app` without an editable install.

### Typer root (`cli/app.py`)
The root `typer.Typer(name="regin", …)` app is assembled here, and the module stays small on purpose — command bodies live next to their domain under `cli/commands/`. Two wiring conventions:

- **Flat top-level commands** — module exposes `register(app)`, e.g. `db.register(app)` (`init`, `rebuild`, `tags`, `search`), `meta.register(app)` (`doctor`), `server.register(app)` (`serve`).
- **Grouped subcommands** — module exposes a pre-built sub-Typer added via `app.add_typer(...)`, giving `regin <group> <cmd>` forms (e.g. `users`, `topics`, `trace`, `logs`, `memory`, `grader`).

A `@app.callback()` root runs before **every** command: it calls `configure_logging()` + `configure_activity_log()` once, then `_stamp_cli_invocation` logs `command_invoked` and arranges a `call_on_close` finalizer that records `command_completed` / `command_failed` with a duration (clean `SystemExit(0)` from `--help` is not treated as a failure). Every CLI run is therefore traceable in the activity log under `feature=cli`.

### Shared helpers
- **`cli/deps.py`** — the `@require_db` decorator centralizes the "is the DB initialised?" guard (prints `Run 'init' first.` and exits 1) that was previously copy-pasted across handlers. Signature-agnostic: it forwards all args untouched.
- **`cli/output.py`** — `echo` / `error` / `table` write to module-level `_stdout` / `_stderr` sinks so tests can monkeypatch a `StringIO`. `table` is a thin whitespace-aligned renderer — no heavyweight dependency. CLI output is UX; anything diagnostic should flow through structured logging instead.

### Canonical ops commands
- **`regin doctor`** (`cli/commands/meta.py` → `lib/doctor.py`) — environment health check: scans for required/optional CLI tools and provider hook scripts, prints grouped ✓/✗/⚠ rows with install hints, and reports project-local readiness.
- **`regin init` / `regin rebuild`** (`cli/commands/db.py` → `lib/db_rebuild.py`) — the DB is a **derived cache**. `rebuild` reconstructs `repos`, `branches`, `pattern_docs`, `tags`, `doc_tags` from git-tracked authoritative files (`patterns/*/SKILL.md`, `.grit/rules.json`, `config/tags.yaml`, `config/settings.json`) while preserving local-only tables (`experiments`, `rule_triggers`, `users`, `audit_log`). Note the schema-drift gotcha from CLAUDE.md: `init` builds from `db/schema.sql`, not Alembic.

### Add-a-command playbook
1. Create `cli/commands/<domain>.py` with either a `register(app)` (flat) or a `<domain>_app = typer.Typer(...)` (grouped).
2. Import and wire it in `cli/app.py`.
3. Gate DB-dependent handlers with `@require_db`; emit user output via `cli/output.py`, diagnostics via the activity log.

---

## 2. Ops observability: activity log, structured logging & audit trail

This stack records **regin's own operations** (CLI, web, hooks, DB writes). It is *not* `lib/trace/`, which records Claude Code agent session traces — the two are easy to conflate.

### Activity log (`lib/activity_log.py`)
One rotating JSONL stream at `settings.log_dir/regin.log`, every line tagged `feature=<name>`. Obtain a logger with `get_activity_logger("<feature>")`:

- **`log.read(event, **kw)` → DEBUG**, **`log.write(event, **kw)` → INFO**. `.info()`/`.debug()` are deliberately absent and raise `AttributeError` — the read/write split is the API.
- **Secret-looking keys** (`password`, `token`, `api_key`, `authorization`, `cookie`, …) are auto-redacted as defense in depth.

Inspect it from the terminal via the `regin logs` group (`cli/commands/logs.py`):

| Subcommand | Purpose |
|---|---|
| `list` | per-feature counts (events, errors, last seen) |
| `tail` | last N lines, filterable by feature/level; `--since 1h\|30m\|2d` cutoff |
| `grep` | regex search across the stream |
| `prune` | delete rotated archives older than a cutoff |
| `path` | print the absolute active log path |

### Structured logging bootstrap (`lib/logging_setup.py`)
`configure_logging()` (called once from the CLI root callback and at web/process start) wires both stdlib `logging` and `structlog` so third-party libs (Flask, SQLAlchemy, urllib) emit into one pipeline. Two rendering modes: coloured console (dev / `REGIN_LOG_FORMAT=console`) vs. JSON. Use `get_logger(__name__)` for ordinary structured logging; reserve the activity log for ops *history* that should be queryable by feature.

### Audit trail (`lib/audit.py`)
Records who changed what in the **web dashboard** into the `audit_log` table. Writes are best-effort (never block the main operation), reads never surface a 500 (observability-only, not load-bearing). Both sides route through `lib.orm.AuthSessionLocal()` — the same dispatch as user-CRUD, so it follows SQLite-in-standalone / MySQL-in-shared mode.

### Where to look first
- "Why did this CLI run do X?" → `regin logs grep <event>` (filter `feature=cli`).
- "Is my environment set up?" → `regin doctor`.
- "Who changed this in the dashboard?" → `audit_log` table via `lib/audit.py` readers.

---

*Adjacent, intentionally elsewhere:* token pricing/estimation (`lib/tokens/`) is named in the bucket blurb but its billing semantics live under the approved **trace-usage-billing** topic; agent-facing ops messaging lives under the **agent-memory** / inbox surfaces.