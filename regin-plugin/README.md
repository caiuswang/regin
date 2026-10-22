# regin-agents — Claude Code plugin prototype

Packages regin's goal-verified build loop so it can be installed into **any
repo** via Claude Code's plugin system, instead of relying on hardcoded paths in
`.claude/skills/`.

## What's in the bundle

`regin-plugin/` is the marketplace root (add THIS path) — its
`.claude-plugin/marketplace.json` lists `plugins/regin-agents` and the mods
(see "Mods" below). The `regin-agents` plugin's own `.claude-plugin/plugin.json` gives it the
`regin-agents:` namespace; its `skills/` and `agents/` directories are
auto-discovered by Claude Code (no manifest listing needed), its `.mcp.json`
wires up the memory + regin + topics MCP servers, and `bin/regin-mcp.sh` is the
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
/plugin marketplace add ~/regin/regin-plugin   # or $REGIN_HOME/regin-plugin
/plugin install regin-agents@regin-local
/plugin list                      # confirm enabled
# then: /agents shows regin-agents:goal-verifier, and the skills are invocable
```

If your regin checkout is not at `~/regin`, point the plugins at it — the MCP
launcher and the `regin-band` / `regin-bridge` mods all read the same variable:

```bash
export REGIN_HOME=/path/to/regin   # default is ~/regin
```

## Mods

A mod is a plugin whose `hooks/hooks.json` lists function-hook modules
(`register(on)`, Claude Code ≥ 2.1.289) instead of — or beside — skills and
agents. Each one lives in its own `plugins/<mod>/` directory with a
`.claude-plugin/plugin.json`, and has exactly one entry in
`.claude-plugin/marketplace.json`; `tests/mods/test_marketplace.py` fails when
the two drift apart.

| Mod | What it adds |
|---|---|
| `regin-band` | An `AbovePrompt` band, `regin inbox: N unread`, refreshed on session start and at each turn's end from `regin messages stats`; hidden when nothing is unread |
| `regin-bridge` | The agent bridge's Mod channel: on session start it spawns `regin bridge listen --session <id>` once and submits each line it prints as a prompt. While that child is connected, `deliver()` sends the session's bridge messages through it (audited `channel=mod`) instead of typing into its tmux pane; needs `regin serve` running and `agent_bridge.token` set |
| `regin-permit` | A `tool.check` hook: when the engine's verdict is `ask` on a real call (not `AskUserQuestion` / `ExitPlanMode`, whose dialog a plugin cannot dismiss), it shows a band, raises a notification and runs `regin bridge hold`, so the call's card on `/live` offers Allow / Deny. Allow or deny becomes the verdict (a deny's reason is the model's error); the band's **Answer here**, an unreachable regin, or the hold's 10-minute ceiling hands the call back to the native dialog |
| `regin-attribution` | An `attribution.text` hook: for `commit` and `pr` it takes the text Claude Code composed and drops every `Claude-Session:` line and bare `https://claude.ai/code/session_…` link, leaving the rest verbatim; `exemption` and `remedy` pass through |

Install a mod the same way as any plugin in this marketplace:

```bash
/plugin marketplace add ~/regin/regin-plugin   # once
/plugin install regin-band@regin-local
```

Check one from the shell before installing it:

```bash
claude plugin validate regin-plugin/plugins/regin-band
claude plugin test regin-plugin/plugins/regin-band     # runs hooks/*.test.ts[x]
```

Edit mod source here, never under `~/.claude/dev-mods/`: a dev-mod raises a
hot-reload confirmation in the operator's session, which stalls an unattended
run.

`regin-band` and `regin-bridge` currently run regin from a fixed checkout path
(`REGIN` in each `hooks/register.ts[x]`); change that constant if your checkout
lives elsewhere.

## The boundary (read this)

A Claude Code plugin **cannot guarantee an external CLI is on PATH** and cannot
declare system/pip dependencies. regin's skills depend on regin two ways:

| Dependency | Bundled here? | Status |
|---|---|---|
| **`memory` / `regin` / `topics` MCP servers** (`index_*`, `recall`, `send_to_user` + the feature/task/defect tools, topic tools) | ✅ yes, via `.mcp.json` + `bin/regin-mcp.sh` | the recall arm + progress messages work as soon as the plugin is enabled (given `REGIN_HOME`) |
| **`regin` CLI** (`regin gate recall-ran`, `regin route`, `regin goal feedback`, `regin topics wiki-debt`) | ⚠️ not bundled | `scripts/setup.sh` links the `regin` console script into `~/.local/bin` (override with `REGIN_BIN_DIR`), so a set-up checkout puts it on PATH for every repo. A machine that skipped setup still has no `regin`. |

So this prototype makes the **MCP-backed** half of the loop portable, and pins
the **CLI-shell-out** half to one requirement: a regin checkout that ran
`scripts/setup.sh`. Both halves still resolve through that checkout —
`bin/regin-mcp.sh` localizes its path to a single file so you can see exactly
where that contract lives.

The failure mode this guards against is asymmetric, not total: regin's hooks
carry an absolute interpreter path, so the sensor half keeps working in every
repo while an unlinked CLI makes every skill's gate leg fail — a harness that
half-works without saying so.

## Notes

- The skills **and agents** bundled here are the portability-fixed copies: bare `regin` (no
  absolute-path / `.venv/bin/python` fallbacks), and regin-repo-only
  *conventions* genericized — the machine-gate floor reads "your repo's test +
  lint/complexity gates" rather than regin's `pytest`/radon/grit, and the
  convention-skill routing points at "your repo's file→convention mapping"
  rather than regin's `CLAUDE.local.md` table. The agents got the same
  treatment: `goal-builder`/`goal-verifier` name "your repo's test runner,
  scoped to the diff" instead of `.venv/bin/python -m pytest`, keeping the
  durable advice (scope to the diff, parallelise, foreground with a timeout,
  paste verbatim output) that the literal command was carrying. This is why the bundled copies
  intentionally diverge from `.claude/skills/` (which stay regin-specific
  because they run *in* the regin repo) — don't "sync" them back verbatim.
- regin-repo-only skills (`generated/*`, `regin-python-conventions`,
  `frontend-style-convention`, `debug-hooks`) are deliberately **excluded** —
  they describe regin's own internals and aren't meant to run elsewhere.
  `run-regin` is excluded for the same reason; it currently exists only in the
  patterns store (`regin pattern` / `~/.local/share/regin/patterns/run-regin`),
  not deployed to this repo's `.claude/skills/`.
