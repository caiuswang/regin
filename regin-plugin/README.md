# regin-agents — Claude Code plugin prototype

Packages regin's goal-verified build loop so it can be installed into **any
repo** via Claude Code's plugin system, instead of relying on hardcoded paths in
`.claude/skills/`.

## What's in the bundle

`regin-plugin/` is the marketplace root (add THIS path) — its
`.claude-plugin/marketplace.json` lists the single `plugins/regin-agents`
plugin. That plugin's own `.claude-plugin/plugin.json` gives it the
`regin-agents:` namespace; its `skills/` and `agents/` directories are
auto-discovered by Claude Code (no manifest listing needed), its `.mcp.json`
wires up the memory + send-to-user MCP servers, and `bin/regin-mcp.sh` is the
`${CLAUDE_PLUGIN_ROOT}`-relative launcher those servers run through (see "The
boundary" below).

This solves the two gaps from the portability discussion natively:

1. **Agents ship with skills.** Installing the plugin makes `goal-refiner`,
   `goal-builder`, `goal-verifier` available (as `regin-agents:goal-verifier`),
   so the goal-verified agent-arm works in the target repo — no separate
   agent-deploy mechanism needed.
2. **No hardcoded paths.** `.mcp.json` references the server launcher via
   `${CLAUDE_PLUGIN_ROOT}` (resolved to the versioned plugin-cache dir at
   install time), not an absolute path.

## Try it locally

```bash
# from anywhere, inside any repo you want to test in:
/plugin marketplace add /Users/taowang/regin/regin-plugin
/plugin install regin-agents@regin-local
/plugin list                      # confirm enabled
# then: /agents shows regin-agents:goal-verifier, and the skills are invocable
```

To test in a *different* repo, point the launcher at your regin checkout:

```bash
export REGIN_HOME=/Users/taowang/regin   # default is ~/regin
```

## The boundary (read this)

A Claude Code plugin **cannot guarantee an external CLI is on PATH** and cannot
declare system/pip dependencies. regin's skills depend on regin two ways:

| Dependency | Bundled here? | Status |
|---|---|---|
| **`memory` / `send-to-user` MCP servers** (`index_*`, `recall`, `send_to_user`) | ✅ yes, via `.mcp.json` + `bin/regin-mcp.sh` | the recall arm + progress messages work as soon as the plugin is enabled (given `REGIN_HOME`) |
| **`regin` CLI** (`regin gate recall-ran`, `regin route`, `regin goal feedback`, `regin topics wiki-debt`) | ❌ no | still requires `regin` installed on PATH in the target repo |

So this prototype makes the **MCP-backed** half of the loop portable, and leaves
the **CLI-shell-out** half as the explicit remaining contract. `bin/regin-mcp.sh`
localizes the regin-checkout path to a single file so you can see exactly where
that contract lives.

## Notes

- The skills bundled here are the portability-fixed copies (bare `regin`, no
  absolute-path fallbacks).
- regin-repo-only skills (`generated/*`, `regin-python-conventions`,
  `frontend-style-convention`, `debug-hooks`) are deliberately **excluded** —
  they describe regin's own internals and aren't meant to run elsewhere.
  `run-regin` is excluded for the same reason; it currently exists only in the
  patterns store (`regin pattern` / `~/.local/share/regin/patterns/run-regin`),
  not deployed to this repo's `.claude/skills/`.
