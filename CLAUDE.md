# CLAUDE.md

regin is a harness for AI coding agents: local pattern guides, lint/rewrite engines wired into hooks, and a session-trace UI. See `README.md` for setup, `ARCHITECTURE.md` for internals, `AGENTS.md` for the agent-facing overview.

## Commands

```bash
# CLI (all from repo root)
.venv/bin/python cli/regin.py init          # Initialize DB + directories
.venv/bin/python cli/regin.py doctor        # Environment + missing CLI tools
.venv/bin/python cli/regin.py add-repo <path>
.venv/bin/python cli/regin.py rebuild       # Rebuild DB from git-tracked files
.venv/bin/python cli/regin.py serve         # Web dashboard on :8321

# Frontend (Vue 3 SPA in frontend/, Flask serves /api)
cd frontend && npx vite                                  # dev on :5173, proxies /api → :8321
cd frontend && npx vite build                            # → web/static/dist/
cd frontend && ./node_modules/.bin/playwright test       # E2E
```

Use the `.venv` interpreter; do **not** invoke bare `python` — the system interpreter lacks the project's deps.

## Conventions

### Settings

- All paths/flags come off the `settings` singleton in `lib/settings.py` (pydantic-settings). Don't read env vars directly.
- `reload_settings()` mutates the singleton in place — captured `from lib.settings import settings` references stay live.

### Database

- New code uses the SQLModel layer in `lib/orm/`. Call `SessionLocal()` / `AuthSessionLocal()`.
- Raw `sqlite3` is reserved for the paginated trace reads in `lib/orm/engine.py` that can't be expressed cleanly in SQLModel.
- **Schema drift gotcha**: `regin init` builds the DB from `db/schema.sql`, **not** Alembic. Any migration that adds tables/columns must also be folded into `db/schema.sql`, or fresh installs will diverge.

### Activity logging

Use `lib/activity_log.py` for regin's own ops history (CLI, web, hooks, DB writes). Distinct from `lib/trace/`, which records Claude Code session traces.

```python
from lib.activity_log import get_activity_logger
log = get_activity_logger("patterns")
log.read("pattern_loaded", pattern_id=pid)      # DEBUG
log.write("pattern_imported", pattern_id=pid)   # INFO
log.error("import_failed", exc_info=True)
```

- **read = DEBUG, write = INFO.** `.info()` / `.debug()` are deliberately absent on the wrapper and raise `AttributeError` if called.
- Secret-looking keys (`password`, `token`, `api_key`, `authorization`, `cookie`, …) are auto-redacted. Defense in depth, not your primary control.

### Patterns

- Patterns are user-authored. Edit the body directly; regin only writes frontmatter at creation.
- `manual: true` in frontmatter marks a pattern as user-owned — the deployer respects it.
- When embedding code in a pattern, condense: strip imports, show 2-3 representative fields, omit getters/setters.

### Rule engines

Configured under `settings.rule_engines` (see `ARCHITECTURE.md`). An empty list ships no lint chrome (no `grit-rules` skill, no PostToolUse enforcement, no `/api/rules`). Grit rules use two layers of false-positive protection: a GritQL class-name guard inside each pattern, and `scripts/filter_grit_output.py` for trigger-based post-filtering.
