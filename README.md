# regin

**Harness infrastructure for AI coding agents.**

🌐 **[regin.ccday.top](https://regin.ccday.top)** — the official project site.

> ⚠️ **Early beta.** regin is under active development and breaking changes are expected at any time. Database schemas, settings keys, hook contracts, skill bundles, and CLI flags may all change without backward-compatible shims. Pin a commit if you need stability.

regin is not an agent platform. It's the layer *around* an agent — the **harness**. The framing that crystallized this for the community is *Agent = Model + Harness* ([LangChain: The Anatomy of an Agent Harness](https://www.langchain.com/blog/the-anatomy-of-an-agent-harness)), and Birgitta Böckeler's [Harness Engineering for coding agent users](https://martinfowler.com/articles/harness-engineering.html) is the clearest write-up of what that means in practice. regin is the harness half.

A harness turns a non-deterministic model into a teammate you can trust on real work. It does this through two complementary mechanisms:

- **Guides (feedforward)** — skills and docs that steer the agent *before* it acts.
- **Sensors (feedback)** — hooks that observe the agent *as* it acts, and force corrections when it drifts.

regin gives you both, plus the observability to know whether either is actually working.

## What regin provides

### Pattern & skill management — the feedforward layer

regin's feedforward layer has two stages, raw → refined:

- **Patterns** are local procedure guides written or imported by you. Add a guide for a recurring implementation shape — a controller, a repository, a migration test, whatever convention you want the agent to follow — and regin tracks it, tags it, and deploys it into the active provider's skills directory. Patterns are the raw material: opinionated, local to your machine, edited as your codebase evolves.
- **Skills** are the promoted, versioned, shareable form. When a pattern earns its keep, `pattern promote` packages it as a versioned skill bundle that can be published, deployed across machines, and surfaced to the agent only when its trigger conditions match — not as static prose the agent has to re-read every session.

The split matters because the lifecycle differs. Patterns live next to the code they document; skills get cut, reviewed, and shipped on their own cadence. You don't promote everything — only the patterns that have proven worth carrying forward.

Both are advisory by nature: they shape what the agent *tends* to do.

### Rule management — the feedback layer

Rules are the *enforced* half of the harness. They are not documentation the agent is asked to remember. They are real Claude hooks that intercept tool calls, edits, and prompts in flight, evaluate them against your project's invariants, and either push the agent back on-spec or block the action outright.

The point is force, not suggestion. A rule that says "never edit generated files" doesn't live in a doc you hope the agent re-reads — it lives in a `PreToolUse` hook that refuses the Edit and tells the agent why, in the same turn. The agent can't drift past it because the harness, not the agent, makes the call.

This closes a gap most setups have: feedforward-only agents encode conventions in prose and then have no way to find out whether the agent honored them. regin's rules turn conventions into enforced control points.

### Session tracing — the observability layer

Hooks and transcripts produce a stream of events: tool calls, model thoughts, file edits, hook decisions, token usage. The terminal flattens all of that into a scrollback you can't search, slice, or replay. regin's web UI ingests the same stream and gives you a real session viewer — filterable by tool, by agent, by phase; replayable; with rollups (tokens by tool, advisor calls, skill reads) so you can diagnose *why* a session went sideways instead of just noticing that it did.

Patterns, skills, and rules are guesses about what the agent needs; trace is how you find out which guesses were right.

### Mobile remote control — *experimental*

> ⚠️ **Experimental, not yet mature.** Rough edges, thin coverage, and breaking changes are expected — treat it as a preview, not something to depend on.

The web UI ships a mobile-first `/live` view: a phone-sized card that tails a running session — the agent's current state, its recent steps, and any question it's blocked on. When the session's terminal is reachable over the **agent bridge** (an HTTP request relayed as guarded keystrokes into the live agent's tmux pane), that card doubles as a remote control: from your phone you can answer the agent's permission prompts and steer it while it runs. See [docs/agent-bridge-design.md](docs/agent-bridge-design.md) for the design.

### Agent memory — cross-session learning

Trace tells you what happened inside one session; **agent memory** is how the lessons from past sessions reach the next one. When a session discovers something worth keeping — a non-obvious root cause, a gotcha, a decision and the reasoning behind it — it's distilled into a lesson (explicitly, when the agent flags one, or automatically by a post-session distiller) and written to a cross-session store that lives *outside* any single prompt. On later tasks the relevant lessons are recalled and injected into context on demand, matched to what the agent is actually doing rather than pasted in wholesale.

The store is curated, not an append-only log: lessons are ranked by usefulness, reinforced when they actually get recalled, de-duplicated and consolidated in an offline pass, and superseded or retired as they go stale — all browsable, searchable, and human-approvable from the web UI. The payoff is a harness that sharpens with use: the mistake one session made becomes the guidance the next session starts with. See [ARCHITECTURE.md](ARCHITECTURE.md#agent-memory-cross-session-experience) for internals.

### Topic wikis — per-repo knowledge

Each registered repo gets its own topic wiki: a store of repo-specific knowledge (architecture notes, gotchas, conventions, runbooks) organized as a graph of topics that lives alongside the code rather than in the agent's prompt. Topics aren't prose you hand-maintain: a tool-using agent explores the repo and *proposes* draft topics, you review them — with an optional agentic review note that checks each draft against the live code — and approve them into the graph, and every approved topic carries a wiki page kept honest by content-drift detection that flags pages whose underlying files have moved on.

At task time, topics are routed to the agent by keyword match over the approved graph and pulled into context on demand — only the slices that touch what it's doing right now, instead of being statically pasted in. The goal is simple: keep the knowledge each repo has accumulated *with* that repo, as a reviewable, self-refreshing artifact rather than prose that quietly rots.

## Run a task with Kimi

A task can be armed on Kimi Code instead of Claude.

1. **Install and sign in.** `kimi` on `PATH` (`regin doctor` lists it under *Task launch providers*), then `kimi login` once.
2. **Arm.** In the task's arm panel pick **Kimi Code** in the provider picker; it is greyed out, naming the missing binary, when `kimi` isn't installed. Only the fields Kimi declares are shown (a model id, free text) — no permission mode or effort.
3. **Pick a launch.**
   - **ACP** — regin drives `kimi acp` as a child of `regin serve`. Stopping or restarting the server ends the run.
   - **tmux** — `kimi` runs in a tmux session on regin's own tmux server, so a `regin serve` restart does not interrupt it.
4. **Watch it.** The run shows on `/live` like any session, with steer and Stop. To look at the pane itself, attach to regin's tmux server (socket `regin-bridge` by default, `settings.agent_bridge.attach_socket`): `tmux -L regin-bridge attach -t task-<task id>-a<attempt>`. Detach with `Ctrl-b d`; closing your client never ends the session.

Known limits:

- Runs are unattended: panes start with `--yolo`, and an ACP run answers approval and question requests with `cancelled`.
- A steer into a running turn is cancel-then-re-prompt; the ACP protocol exposes no mid-turn injection.
- `codex` and `generic` cannot be armed — arming them is refused rather than run as Claude.
- `/live` can launch and Resume Kimi sessions over ACP, but its *terminal* session type still opens only Claude.
- A new worktree needs Kimi's workspace trust; regin writes the record before launch, so no Trust dialog should appear.

Design and the per-provider declaration: *Provider launch capabilities* in [ARCHITECTURE.md](ARCHITECTURE.md).

## What regin is not

Not a chat UI, not an agent runtime, not a model. It assumes you already have an agent and a codebase, and it makes that pairing more productive. The question regin answers is *"how do I keep this agent on-spec across a real team and a real repo,"* not *"how do I run an agent."*

## Supported agents

**Claude only, today.** regin's rule layer depends on hooks deep enough to intercept tool calls, edits, and prompts in flight — and Claude is currently the only widely-available agent with a hook system mature enough to support that. Codex and others are scaffolded as provider stubs (see [docs/setup.md](docs/setup.md#agent-provider-architecture)) but are not yet wired through the rule layer; we'd rather support one agent well than four agents badly.

## Getting started

Install, configure, and run regin: [docs/setup.md](docs/setup.md).

## Acknowledgements

- [Qoder Repo Wiki](https://docs.qoder.com/user-guide/repo-wiki) — inspiration for the per-repo topic wiki design.
- [SkillRouter](https://github.com/zhengyanzhao1997/SkillRouter) — inspiration for embedding-based skill/pattern routing.
- [GritQL](https://github.com/biomejs/gritql) — the query language powering regin's grit rule engine.

## Further reading

- [docs/setup.md](docs/setup.md) — install, configuration, modes, CLI reference, troubleshooting.
- [ARCHITECTURE.md](ARCHITECTURE.md) — system internals, module layout, procedure types, rule engines.
- [docs/spec-notation.md](docs/spec-notation.md) — the notation regin specs are written in: types, state machines, decision tables and invariants instead of prose, worked through on Task, Feature Ledger and Session.
- [docs/defect-task-feature.md](docs/defect-task-feature.md) — how a defect, a task and a feature join: the feature key is the only edge, which key a task holds, and the scopes a reported defect is readable in.
- [docs/topics/proposals.md](docs/topics/proposals.md) — topic graph proposals via external tool-using agents.
- [docs/topics/multi-user.md](docs/topics/multi-user.md) — sharing approved topics across users via git without a shared database.
- [website/README.md](website/README.md) — the standalone Vue 3 marketing/docs site (own build and deploy target).
- [CLAUDE.md](CLAUDE.md) — instructions for AI agents working in this codebase.
