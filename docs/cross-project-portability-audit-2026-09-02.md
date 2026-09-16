# Cross-project portability audit — 2026-09-02

One-time audit of what actually happens when regin's harness runs in a repo
that is **not** regin. Method: drive a real Claude Code session from
`/Users/taowang/typescript-project/deepseek-harness` (a TypeScript/pnpm repo,
already registered in `settings.repo_paths`), then check each layer of the
harness against observed behaviour — `which regin`, live `recall` output,
`~/.claude.json` and `installed_plugins.json` state, and the `regin-agents`
plugin source vs its install cache.

**Conclusion: the substrate is genuinely cross-project; the delivery and the
skill/agent content are not.** Hooks, the memory DB, and the MCP servers all
work from any cwd today — the corpus already holds `repo:aural-kids` (33) and
`repo:oh-my-pi` (9) memories. What breaks is asymmetric and silent: the sensor
half keeps running while the CLI half is absent, and the deliberate recall
paths ignore the scope policy that the auto-inject path honours.

## Per-layer findings

| Layer | Verdict | Evidence |
|---|---|---|
| Hooks (`hook_manager`) | **Portable** | Every command in `~/.claude/settings.json` carries an absolute interpreter path (`/Users/taowang/regin/.venv/bin/python -P -m hook_manager …`), so all 27 registered events fire in any repo. Observed: the harness created `.regin/topics/topics/_meta.json` inside deepseek-harness. |
| Memory DB | **Portable** | `memory_db_path()` resolves through `settings.main_worktree`, which `_main_worktree(_PROJECT_ROOT)` derives from `lib/settings.py`'s own `__file__` — not cwd. One corpus, reachable from every repo. |
| MCP servers (`memory`, `send-to-user`) | **Portable** | `lib/memory/mcp_server.py` prepends its own `_PROJECT_ROOT` to `sys.path`, so an arbitrary spawn cwd works. Both are registered user-scope in `~/.claude.json` and were live in the test session with no regin install in that repo. |
| `regin` CLI | **Broken everywhere but regin** | `pyproject.toml:52` declares the console script, so `pip install -e .` writes `.venv/bin/regin` — reachable only with the venv activated. `which regin` returned not-found in a session where the hooks were running normally. The bundled skills shell out to it at ~25 call sites (`regin gate recall-ran`, `regin route`, `regin goal preflight/feedback`, `regin memory recall`, `regin session-id`). |
| Deliberate recall scoping | **Not wired** (fixed, #5 below) | `resolve_recall_scope` / `resolve_write_scope` have exactly two callers, both in `hook_manager/` (`memory_recall.py:380`, `post_tool_trace.py:952`). `lib/memory/mcp_server.py` and `cli/commands/memory.py` never import `lib.memory.scoping`; their `scope` argument defaults to unset. So `scope_policy` governs the passive `<recalled_experience>` injection only — every recall a *skill* performs is unscoped. |
| Scope filter semantics | **Inconsistent** (fixed, #6 below) | The recall path unions the repo with `global` (`store.py:1378`, `Memory.scope.in_(["global", scope])`), but the browse/topic paths filter strictly (`store.py:627`, `983`, `1007`, `1110`, `Memory.scope == scope`). A scoped tree-nav walk therefore cannot see `global` memories that the same query would surface through `recall`. |
| `regin-agents` plugin content | **Repo-specific** | `agents/goal-builder.md` and `goal-verifier.md` prescribed `.venv/bin/python -m pytest -q -n auto --dist=loadfile` as *the* gate; `goal-verified-treenav/SKILL.md` told the reader preflight "needs the repo's `.venv`". The genericization recorded in memory `c53df2a9` covered the skills only — the agents that do the building and verifying kept the Python commands. |
| Plugin distribution | **Stale by construction** | The install cache is keyed by version (`~/.claude/plugins/cache/regin-local/regin-agents/0.1.0`, installed 2026-07-02). `plugin.json` stayed at `0.1.0` while the source gained the `topics` MCP server and `goal-verified-treenav/REFERENCE.md` and diverged in every remaining file, so an install serves the July snapshot. |
| MCP ownership | **Duplicated** | `memory` and `send-to-user` are defined both user-scope in `~/.claude.json` and in the plugin's `.mcp.json`. Installing the plugin exposes both copies (the plugin's under `plugin:regin-agents:`), doubling those tool schemas in context. `topics` exists only in the plugin. |

## Fixes applied

1. **`scripts/setup.sh` step 1b** links the `regin` console script into
   `$REGIN_BIN_DIR` (default `~/.local/bin`) and warns when that directory is
   not on PATH. This is the whole fix for the CLI layer: `pip install -e .`
   already produced the binary, nothing ever exposed it.
2. **`config/settings.local.json`** sets `agent_memory.scope_policy` to
   `per-repo`, so auto-injected recall narrows to the current repo plus
   `global` instead of ranking every repo's lessons together.
3. **Plugin agents genericized** — `goal-builder`, `goal-verifier`, and
   `goal-refiner` now name "your repo's test runner, scoped to the diff"
   and keep the transferable advice (scope to the diff, parallelise, run in
   the foreground under `timeout`, paste verbatim output) that the literal
   `pytest` command was carrying. `goal-verified-treenav` no longer tells a
   non-Python repo to run preflight through `.venv/bin/python`.
4. **`plugin.json` bumped to 0.2.0** so the version-keyed cache cannot serve
   the July snapshot. The `0.1.0` cache directory is left in place because
   `installed_plugins.json` still points `oh-my-pi` at it; that install picks
   up the new version on `claude plugin update`.
5. **`resolve_tool_scope`** (`lib/memory/scoping.py`) now backs the deliberate
   front-ends: the four `recall`/`index_*` MCP tools and the five `regin
   memory` retrieval commands. An explicit scope still wins, `"all"` is the
   escape hatch that searches every repo, and an omitted scope applies the
   policy — so `per-repo` reaches what a *skill* recalls, not just the
   injected block. The MCP server resolves it from `os.getcwd()`: every
   running server process was observed with cwd set to the repo its session
   launched in, which is the signal the docstring's "arbitrary cwd" caveat
   said not to *assume* — checked, not assumed.
6. **`global` unioned into the scoped topic lookups** —
   `memories_for_topic_node`, `memories_for_topic_subtree` and
   `orphaned_memory_ids` now match `_eligible_rows` (`repo` + `global`).
   Without this, #5 would have emptied the tree walk in any repo whose own
   memories have not accumulated yet while 432 `global` rows sat hidden.
   `list_memories` keeps `== scope`: it is the `/memory` UI's filter facet,
   where selecting one repo must mean that repo only.
## Open, deliberately not fixed

- **Registration remains a manual gate.** `_repo_name_for` matches cwd against
  `settings.repo_paths`; an unregistered repo silently writes to `global`,
  which is unrecoverable per-memory after the fact. The 432 `global` rows are
  partly this. A capture-time warning when cwd is a git root outside
  `repo_paths` would make the gap visible without changing the policy.
