# hook_manager — Unified hook dispatcher (Claude-event schema)

A single Python entry point (`python -m hook_manager <EventName>`) that owns
hook behavior across the 26 Claude Code hook events
(https://code.claude.com/docs/en/hooks). Most events have concrete
handlers today; the provider-kind hooks (`WorktreeCreate`, `WorktreeRemove`,
`Elicitation`, `ElicitationResult`) are intentionally unwired — registering
them without a real provider causes Claude Code to fail (the harness parses
the hook stdout as a path/payload, and the runner's default
`{"suppressOutput": true}` JSON response gets interpreted as that path,
breaking `EnterWorktree` with `chdir ENOENT`). See `registry.py` for the
live handler list.

In the current architecture, event payload semantics are Claude-spec, while
settings/log/traces paths are resolved by the active provider adapter
(`settings.active_provider`, default `claude`).

## Quick tour

```
hook_manager/
  settings.example.json         — drop-in hooks block for Claude settings.json
  core.py                       — HookPayload, HookResponse, Handler, matchers
  merge.py                      — response-precedence rules
  runner.py                     — stdin → dispatch → stdout pipeline
  registry.py                   — STANDARD registrations (team-wide policy)
  custom_registry.py            — USER-OWNED registrations (env-dependent, opt-in)
  custom_registry.example.py    — template for a fresh custom registry
  handlers/                     — one module per handler behavior
  migration_preview.py          — report what a settings.json swap would change
  e2e_smoke.sh                  — end-to-end harness against real `claude`
  tests/                        — pytest suite
```

## Standard vs. custom handlers

- **`registry.py`** — git-tracked, ships with the repo. Team-wide policy:
  `rule_check`, `session_lifecycle`, `post_tool_trace`, etc. No
  user-specific paths or tokens, no toolchain-specific gates.
- **`custom_registry.py`** — **gitignored**, per-user. Contains personal
  paths, webhooks, and ticket-specific pre-commit guards. Loaded via
  `try/except` so deleting or breaking it never takes down the standard
  registry.
- **`custom_registry.example.py`** — git-tracked template. Has commented-
  out examples of `commit_guard` and a minimal custom handler. To start
  using custom hooks:
  ```
  cp hook_manager/custom_registry.example.py hook_manager/custom_registry.py
  # then edit the real file to uncomment / add what you want
  ```

## Run the tests

```
.venv/bin/pytest hook_manager/tests/ -q
```

## Try it manually

```
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"mvn test"}}' \
  | .venv/bin/python -m hook_manager PreToolUse
```
On a machine whose `custom_registry.py` adds the maven guard, this emits
`decision: block` + `permissionDecision: deny` pointing the model at the
maven MCP tools. The standard registry ships no `mvn` gate, so a fresh clone
sees the default no-op response instead — the exact output depends on what
your `custom_registry.py` registers.


## Wiring into Claude

`hook_manager/settings.example.json` is a drop-in `hooks` block for the
provider settings file (Claude default: `~/.claude/settings.json`).
`migration_preview.py` reports the diff between the current settings
file and the manager's expected wiring without modifying anything.
