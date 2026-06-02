# Two capture models: session traces vs. workflow runs

regin records two different things into the **same** `session_spans` /
`sessions` tables, through two deliberately different capture mechanisms.
This doc explains why they differ and records the decision that workflow
capture does **not** need to adopt the session-trace mechanism.

Prerequisites: [`SPAN_DESIGN.md`](./SPAN_DESIGN.md) for the span model and
projection, [`INCREMENTAL_RESCAN.md`](./INCREMENTAL_RESCAN.md)
for the resumable transcript parser.

## The one structural difference

Session traces have **two live sources that race**: hooks fire
synchronously during a session (`hook_manager/`, emitting through
`lib/hook_plugin.py`), while a poll-triggered transcript parser
(`lib/trace/live_rescan.py` → `lib/trace/transcript_usage.py`) reads the
session transcript that hooks can't see (assistant text and thinking have
no hook). The two sources emit overlapping rows — a live placeholder and
the real span for the same prompt/tool/permission. Reconciling that race
is the entire reason for **append-only storage + a read-time merge**
(`lib/trace/merge.py`, then projection).

Workflow runs are **captured from one authoritative source**: a server-side
poller (`lib/trace/workflow_ingest.py`, started as a daemon by
`cli/commands/server.py`) reads on-disk artifacts a workflow run leaves
under the session's `subagents/workflows/<run_id>/` directory — an append
journal while live, a manifest once paused/complete. With a single source and
**deterministic span IDs**, the poller rebuilds a run's spans wholesale (clear
+ reinsert) on each change. The rebuild is idempotent, so there is no
duplicate-row race and nothing for a merge layer to reconcile.

> **Gate: the workflow runtime also fires the full hook suite — we drop it.**
> The Claude Code Workflow runtime fires PreToolUse / PostToolUse /
> PostToolUseFailure / SubagentStart / SubagentStop into the **launching**
> session for *every* workflow-subagent, each tagged
> `agent_type='workflow-subagent'`. Recording those would duplicate the entire
> run into the launching session (one deep-research run ≈ 1,350 spans) and
> flood its conversation view. So the trace hook handlers gate on
> `HookPayload.is_workflow_subagent`: the tool/turn handlers
> (`pre_tool_trace`, `post_tool_trace`, `post_tool_failure`) and the
> `subagent_lifecycle` assistant-response mirror **skip emission**, leaving only
> the lightweight `subagent.start`/`subagent.stop` markers (re-parented under
> the `tool.Workflow` span, folded behind the workflow card in the conversation
> view). The authoritative per-agent detail lives only in the run's own `wf_`
> session. The one positive hook involvement is `post_tool_trace` stamping the
> launched `workflow_run_id` onto the parent's `tool.Workflow` span so the two
> sessions can be cross-linked.

## Diagram — session traces (two sources → merge)

```
  hooks (synchronous)            transcript file (poll-triggered)
  prompt / tool / permission     assistant text + thinking
  + live placeholders            (resumable, byte-offset parse)
          │                                │
          └──────────► session_spans ◄──────┘
                       (append-only; placeholder + real coexist)
                                │
                       READ TIME (pure, no writes)
                       merge: drop superseded placeholders,
                              resolve permissions, stale blockers
                       projection: graft → widen → tree
                                │
                  timeline tree  +  conversation cards
```

## Diagram — workflow runs (one authority → rebuild on poll)

```
  on disk: subagents/workflows/<run_id>/
    journal (append, live)  +  agent transcripts  +  manifest (at pause/done)
                                │
                    poll daemon, mtime-gated
                    terminal? → manifest = authoritative full tree
                    not yet?  → journal = live (flat/confident) tree
                                │
                    clear + reinsert  (deterministic wf* span IDs)
                                │
                       session_spans  →  same projection / serve path
                       (no merge: rebuild is whole and idempotent)
```

## Decision: workflow capture does not need the session-trace refactor

Porting append-only storage + read-time merge onto workflows would add
reconciliation machinery to solve a duplicate-source race that workflow
capture does not have. Symmetry between the two paths is **not** a reason to
change it — the clear-and-reinsert model is correct precisely because the
source is single and authoritative and the span IDs are deterministic.

The shared layer downstream of capture (the `session_spans` schema, the
projection, the serve/render path) is already common to both; only the
capture front-end differs, and that difference is intentional.

### The one transferable piece, and its condition

Both paths read agent transcripts via `lib/trace/transcript_usage.py`, but
through different entry points: session traces use the **resumable**
(byte-offset, incremental) reader; workflow ingest uses the **full** reader
and re-parses every agent transcript on each rebuild. Terminal runs are
skipped by the mtime gate, so this cost is confined to a run's live window.

The resumable reader is worth wiring into workflow ingest **only when** real
runs are large enough for full re-parsing of streaming agent transcripts
every poll to matter — many agents, long transcripts, long-running. For
short, few-agent runs the full re-parse is negligible and the current model
is the simpler correct choice. This is a localized reader swap if the size
threshold is ever crossed, not an architectural change.
