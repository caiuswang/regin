# Uncovered subsystems — proposal batch

Three well-bounded subsystems with no existing topic. Grounding note: the GitNexus index for `regin` was **107 commits behind HEAD** this session and `query` returned no execution-flow processes (only file/symbol matches), so call-edge claims below rest on direct file evidence rather than the graph; no edges were invented. Where GitNexus *did* help, it is cited.

---

## Agent → human message channel (`agent-messages-inbox`)

The durable channel behind the `send_to_user` MCP tool — the one CLAUDE.md tells every agent to call as each step completes.

**The capture path is split on purpose.** A stdio MCP server (`lib/agent_messages/mcp_server.py`) is *session-blind* — it never learns which Claude Code session invoked it — so it does only two things: declare the typed parameter schema (`type`, `title`, `key`, `links`, `supersedes`) and acknowledge the call. The component that *does* know the session is regin's PostToolUse hook: `hook_manager/handlers/post_tool_trace.py` reads the tool input off the landed `mcp__*__send_to_user` span and calls into the store. This keeps the server import-free and instant, and means persistence never breaks on a DB hiccup.

**One writer.** Every write — hook, web API, or test seed — goes through `lib/agent_messages/store.py::record_message`, so supersede-by-`key`, the 16k body cap, link normalization, and push dispatch all happen in exactly one place. The row model and its `MESSAGE_TYPES` severity ladder (`progress · note · lesson · result · summary · warning · blocker`) live in `lib/orm/models/agent_messages.py`; the table + its `trace_id`/`created_at`/`read_at`/`key` indexes are folded into `db/schema.sql` (remember the schema-drift gotcha — `regin init` builds from `schema.sql`, not Alembic).

**Push fan-out is pluggable.** `lib/agent_messages/push/registry.py` exposes `should_dispatch`/`maybe_dispatch` over a `PushChannel` base (`push/base.py`); concrete channels are `push/webhook.py` and `push/telegram.py`, each with its own severity gate, all off by default. Registering a new channel is a one-line append to `_CHANNEL_CLASSES`.

**Surface.** The Flask blueprint `web/blueprints/trace/agent_messages.py` serves the per-session feed (`/api/sessions/<id>/agent-messages`) and the cross-session inbox routes (`/api/agent-messages/inbox`, `unread-count`, `read`, `read-all`, `<id>/ack|dismiss|pin`). The Vue side is `InboxView.vue`, `InboxMessageCard.vue`, and the `useInboxUnread.js` composable driving the live badge. GitNexus corroborated this exact file cluster as one coherent unit.

**Cross-refs.** `type=lesson` messages also tee into agent memory — see [memory-distillation-capture](./memory-distillation-capture.md). The ingest hook is one handler in the broader [trace-span-capture](./trace-span-capture.md) / hooks-injection pipeline.

---

## Loop-engineering: goal preflight → verified build → feedback (`goal-verified-loop`)

The regin-side halves of the `/goal-verified` workflow. The idea: pin a *falsifiable* bar **before** the agent builds, so verification checks against something concrete instead of the agent grading its own homework.

**Preflight (front half, `lib/goal_preflight.py`).** Deliberately **pure-deterministic — no embeddings, no LLM**. A freeform goal string is matched against a fixed `AREA_RULES` table; an `AreaRule` fires on a keyword hit or a path-glob mention (`_fires`/`detect_areas`), and each fired area contributes its `skills`, design `tokens`, and hard `gates` to a `Roadmap`, while `resolve_references` globs the repo for concrete sibling components to mirror. The rules table mirrors the conventions regin's hooks already enforce (RadonEngine, GritEngine, the two bundle engines), so the roadmap never invents a standard the repo doesn't already hold. The CLI is `regin goal preflight "<goal>"` (`cli/commands/goal.py`), with `--json` and an opt-in `--with-lessons` flat-FTS recall leg (demoted 2026-06 to ~22% engagement; structure-first recall is now preferred).

**Feedback (back half, `lib/goal_feedback.py`).** After a verified run it writes two things back to the existing memory store, reusing `remember`/`reinforce` only (no new table): (1) **engagement** — a lesson that made it into the approved acceptance checklist is reinforced, dropped ones are left for reflect's `_decay_chronically_ignored`; this human-approval signal is higher-precision than the trace-referent heuristic in `lib.memory.feedback`; (2) **new lessons** — each FAILED acceptance item is written as an area-tagged `lesson` memory (tag `goal-verified-fail`), phrased as a transferable rule by the skill.

The build + verify halves themselves live in `.claude/skills/goal-verified/SKILL.md` (cited as docs).

**Cross-refs.** The feedback half couples to [memory-engagement-feedback](./memory-engagement-feedback.md) and [memory-recall-pipeline](./memory-recall-pipeline.md); the gates mirror [rule-engine-design](./rule-engine-design.md).

---

## Session-id probe (`session-id-probe`)

A small, self-contained mechanism for a problem with no obvious home: **Claude Code never exposes the live session id to a Bash command**, yet several regin commands (`goal preflight --session-id`, `goal feedback --trace-id`, the `gate` anti-skip checks) need it.

The fix is a durable cache, not command rewriting. Every hook payload *does* carry the session id, so the PreToolUse handler `hook_manager/handlers/session_id_probe.py` calls `lib/session_probe.py::record()` on each Bash call, stamping `{sid, ts, nonce}` into one small JSON file under `settings.data_dir` (atomic temp + `os.replace`; a rare lost update across concurrent hook processes is benign because each write merely re-stamps the *current* session). The always-present `regin session-id` subcommand (`cli/commands/session.py`) then `resolve()`s the freshest entry by cwd, or by an explicit `--nonce`. Because the probe records on the probe command's *own* PreToolUse — which fires immediately before the command runs — a single `SID=$(… session-id)` resolves correctly with no prior step, and works through `$(...)` substitution and the full `.venv/bin/python cli/regin.py session-id` interpreter form alike. Behaviour is pinned by `tests/test_session_probe.py`.

**Cross-refs.** Consumed by the [goal-verified-loop](./goal-verified-loop.md) preflight/feedback CLI; the recording hook is part of the hooks-injection layer.