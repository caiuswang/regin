## Destination

A **feasibility verdict and a throwaway prototype** — on a `prototype/` branch, reachable in the browser — of a real shell terminal rendered *beside* the conversation on the trace page, in a resizable split, with the decisions that shape it locked. The verdict says whether this becomes a `/feature-pipeline` spec or stops. This map decides and prototypes; it does not build the feature.

## Notes

**Domain**: the trace page and its hand-rolled responsive layout (`frontend/src/views/SessionTraceView.vue`, `TraceConversationRegion.vue`, `TraceAgentPane.vue`, `composables/useBreakpoint.js`); the existing terminal tier (`frontend/src/components/trace/SessionTerminalPane.vue`, `views/TerminalPageView.vue`, `web/blueprints/terminal.py`, `lib/agent_bridge/terminal.py`); regin's own tmux server (`lib/agent_bridge/attach.py`, `settings.agent_bridge.attach_socket`); the settings gate (`settings.agent_bridge.terminal_enabled`, admin role).

**Skills every session should consult**: `/grilling`, `/domain-modeling`; `/prototype` for the prototype ticket. Honour `CLAUDE.md` root-cause discipline (no idle timers, no polling) and scope control. Check mature libraries before hand-rolling (a standing preference).

**Tracker**: this map lives on regin's own tasks because Linear's free issue cap was hit while charting it (the map was first opened as CAI-275, with CAI-276 / CAI-277 as its first two tickets; all three are cancelled there and point here). Child tasks are the tickets, `agent_task_deps` is the blocking relation, comments are the resolutions.

### What already exists

A real terminal is already on the branch: xterm.js ↔ WebSocket (single-use ticket auth) ↔ a PTY running a **grouped tmux client** on the session's own claude pane. It is admin-only, off by default, reaches only sessions running in tmux (or taken over onto regin's `regin-bridge` server), and renders as a **tab** of the trace page plus a chromeless `/terminal/:id` page. It does not sit beside the conversation, nothing on the page is resizable, and no layout library is installed. The trace page already has one hand-rolled side-by-side split — the agent companion pane at ≥xl, with a three-column mode at ≥2xl (feed + pane + detail rail) — so "beside" has a precedent.

### Constraints settled while charting

1. **Two pane kinds, shell first.** The terminal the user asked for is a *general shell* in the session's project directory (sessions record their starting `cwd` — `lib/orm/models/trace.py:211`), for running `git status` / tailing logs while reading. The existing claude-pane terminal is the second kind and plugs into the same slot later.
2. **Trace page only.** A resizable split built as a reusable primitive; an app-wide dock system (VS Code-style grid, drag-to-rearrange, panel tabs) is out of scope.
3. **The shell lives in regin's own tmux server** (`regin-bridge` socket), reusing the attach + `terminal.py` plumbing: `pty.fork` + `tmux new-session` with `$SHELL` instead of `claude attach`. Buys reload-survival and reattach for free; keeps one PTY code path. The alternative — a bare PTY with a server-side scrollback ring buffer replayed on reconnect, the VS Code / JupyterLab way — was priced and declined: it loses the shell on `regin serve` restart and needs a session table + replay protocol regin does not have.
4. **The gate stays binary**: admin role + `terminal_enabled` (or a sibling flag — a ticket decides). A general shell is full shell access as the `regin serve` user; no filter over a keystroke stream is a control.
5. **Below `lg` there is no terminal**, matching the existing pane's behaviour; keyboard ownership reuses the existing read-only / in-control switch.

## Decisions so far

<!-- one line per closed ticket -->

* [A resizable split for Vue 3 — library or hand-rolled](/tasks/57) — **reka-ui's `Splitter`**, already installed (2.9.10), zero new deps, full ARIA handle, px min/max, both orientations; xterm refits through the existing ResizeObserver with no splitter event. Runner-up PrimeVue `Splitter` (percent-only, no collapse in 4.5.5). Wrap it in a `TraceSplit.vue` so either swaps in one file. Mount under the `isXl` gate around `[feed | handle | pane]` only, rail stays a sibling. Findings: `docs/research/side-terminal-split.md` @ `7065bd50`.

## Not yet specified

* **The claude-pane terminal as the second pane kind.** Once the slot exists, does the existing Terminal *tab* move into it, stay as a tab, or both? Depends on how the slot turns out in the prototype.
* **Layout persistence.** Split ratio and open/closed state per user in `localStorage` (the `useFilterState` pattern) is the obvious answer; whether the *shell* identity should also persist across trace pages is not, and hangs on the lifecycle decision.
* **More than one terminal in the slot** (tabs inside the pane, one shell per trace vs a project-wide one). Not sharp until the lifecycle ticket answers what a shell is keyed by.
* **The pop-out page.** Whether `/terminal/:id` keeps existing, gains the shell kind, or is retired once the side pane exists.

## Out of scope

* **An app-wide dock / panel system.** Ruled out while charting: it is a multi-week refactor of a 2000-line view with three hand-tuned breakpoint modes, and the question here is whether a terminal beside the conversation is worth having, not whether the app needs docking. Returns, if ever, as a fresh effort with the split primitive as its seed.
* **A non-tmux PTY with buffer replay.** Priced in constraint 3 and declined.
* **Non-admin or read-only shell access.** A shell is a shell; there is no partial version of it.
* **Building the feature.** The destination is a prototype and a verdict; `/feature-pipeline` builds from the spec that may follow.
