# CLAUDE.md

regin is a harness for AI coding agents: local pattern guides, lint/rewrite engines wired into hooks, and a session-trace UI. See `README.md` for setup, `ARCHITECTURE.md` for internals. `AGENTS.md` is a verbatim mirror of this file for agents that read it instead.

## Discipline
When you have enough information to act, act. Do not re-derive facts already established
in the conversation, re-litigate a decision the user has already made, or narrate
options you will not pursue in user-facing messages. If you are weighing a choice, give
a recommendation, not an exhaustive survey. This does not apply to thinking blocks.

## Progress reporting (send_to_user)

Whenever a task involves 3 or more distinct steps (investigate / change / verify counts
as 3), call the `send_to_user` MCP tool as each step completes — even in interactive
sessions where the user is watching. Do not wait until the whole task is done. Open
with a kickoff message stating the goal as you understand it and the planned steps;
then one message per step: what was done, the evidence (test output, file, commit),
and what comes next. Keep each message under ~150 words.

Before sending, audit each claim against an actual tool result from this session; report
only work you can point to evidence for, and say explicitly when something is not yet
verified.

These messages persist in the session's Messages tab **and** the cross-session **Inbox**
(`/inbox`, with a live unread badge), and double as a reviewable trail: when a similar
problem comes up later, retrace past sessions' Messages to see how it was diagnosed and solved.

The tool takes optional typed args beyond `message`: `type`
(`progress`|`note`|`lesson`|`result`|`summary`|`warning`|`blocker` — drives styling and the
webhook gate), `title`, `key` (re-send with the same key to **supersede** a prior message in
place instead of stacking — use it for one advancing progress line), and `links` (file paths /
URLs). High-severity messages can fan out to a webhook (off by default;
`settings.agent_messages.webhook_url`). Internals: *Agent Messages* in `ARCHITECTURE.md`.

`type=lesson` is special: besides landing in the inbox, the message is captured into the
cross-session **agent memory** (`lib/memory/`, browsable at `/memory`, auto-injected as
`<recalled_experience>` into future matching prompts, pulled deeper via the memory `recall`
MCP tool). Send one whenever you learn something a future session should know — a gotcha,
a non-obvious root cause, a decision and its why. To **correct or refresh** an existing
lesson instead of stacking a near-duplicate, pass `supersedes=<memory-id>`: it retires the
old memory (chained via `superseded_by`, hidden from recall but kept for audit) and
replaces it — the non-destructive alternative to `regin memory forget` (a hard delete). The
CLI mirror is `regin memory supersede <old-id> --body … `. Internals: *Agent Memory* in
`ARCHITECTURE.md`.


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

### Agent memory index (MEMORY.md)

The Claude Code session auto-memory index (`MEMORY.md`, in the session's memory
directory) is injected into context every session and has no eviction, so left unchecked it grows
unbounded and inflates the per-session baseline. Treat it as a **capped pinned cache,
not an append log** — this is discipline, not a hook; nothing enforces it for you:

- **Budget ~15 lines.** Reserve always-loaded slots for durable, cross-cutting facts
  only: active project state, behavioral feedback, a few architecture anchors.
- **Route episodic/narrow lessons to recall instead** — capture them via
  `send_to_user(type=lesson)` (regin `lib/memory`) or hindsight; they resurface on
  demand without riding in every prompt.
- **When a save would exceed the budget, evict — don't append.** Move the
  least-relevant or completed-work lines to the sibling `MEMORY.archive.md` (which is
  *not* auto-loaded), and retire project-state lines once the work lands.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **regin** (23476 symbols, 44139 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/regin/context` | Codebase overview, check index freshness |
| `gitnexus://repo/regin/clusters` | All functional areas |
| `gitnexus://repo/regin/processes` | All execution flows |
| `gitnexus://repo/regin/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
