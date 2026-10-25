## Question

A shell in regin's tmux server outlives the browser tab. So what is it keyed by, and what ends it?

Decide, with `/grilling` and `/domain-modeling`:

1. **Identity.** One shell per trace (named for the trace, like `attach.py`'s `_session_name`), per project `cwd` (so two traces of the same repo share one shell), or per user? Recommendation to test: per trace, cwd from the session's `cwd` column, because the terminal is opened *from* a trace page and a shared project shell would surprise on a second tab.
2. **Ending.** Root-cause discipline forbids idle timers. Candidates: an explicit close button that kills the tmux session; the existing reaper (`lib/agent_bridge/reaper.py` — read what it currently sweeps and whether a shell fits its rule); `regin serve` shutdown leaving shells alive on purpose (that is what tmux buys). What does the operator see when a shell died underneath a reconnect?
3. **Missing cwd.** The session's `cwd` may no longer exist, or the trace may be an `sdk-…` run whose cwd is a worktree since removed. Refuse with a structured reason, or fall back to the repo root from `repos`?
4. **The gate.** Reuse `terminal_enabled`, or a sibling `shell_enabled`? The existing flag authorizes attaching to *a pane claude is running in*; a shell is broader. Recommendation to test: a sibling flag, so an operator can allow watching the agent without allowing a shell.
5. **Concurrency.** Two browser tabs on the same trace: two grouped clients on one tmux session (already how `terminal.py` works) — confirm nothing in the shell case changes that.

Resolution is a comment listing each answer and its why; the prototype ticket inherits them.
