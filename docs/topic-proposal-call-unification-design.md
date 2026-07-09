# Topic-proposal call-path unification — design

Status: not started. This document scopes the redesign; no code in this
document has been written.

## Problem

The topic-proposal pipeline (draft → review → drift-triage) is one
conceptual flow, but its three stages currently reach an external agent
through three different mechanisms, each with its own tracing/tagging
behavior:

1. **Drafting** (`topic-proposal-drafting`,
   `lib/topics/proposal_external.py::run_external_agent_proposal`) — Popens
   the configured agent directly, blocks the calling request thread on
   `proc.communicate()` with a long/optional timeout
   (`_proposal_wait_timeout`), and **manually emits its own trace spans**
   (`_emit`, `_emit_session_start`) tagging `agent_type='topic-proposal-agent'`.
   Completion is signalled out-of-band via `regin topics proposal-finish`
   (`lib/topics/proposals/finish.py`).
2. **Review** (`topic-proposal-review`,
   `lib/topics/proposal_review.py`) — goes through the shared
   `ExternalAgentLLM` port (`lib/memory/adapters.py::resolve_proposal_reviewer`),
   in two modes: a blocking one-shot `.complete()` call
   (`generate_review_note`), and a detached mode
   (`start_review_run` → `spawn_spec()` → `_review_agent_worker` on a daemon
   thread) that Popens the agent itself and sets `REGIN_LLM_SURFACE` in its
   env — the same env var `ExternalAgentLLM.complete` sets for the memory /
   grader / topics-split stages, picked up by the SessionStart hook as the
   `llm_surface` span attribute and turned into origin/tags at ingest
   (`_stamp_llm_stage_origins`, `lib/trace/trace_service/ingest.py`).
   Completion is signalled the same way as drafting, via
   `review-finish`.
3. **Drift-triage** (`topic-proposal-drift-triage`,
   `lib/topics/agent_spawn.py::_drift_is_material`) — reuses the *same*
   `resolve_proposal_reviewer()` instance as review and calls `.complete()`
   without overriding `surface_id`, so it inherits the reviewer's bound
   surface id at the trace layer even though its prompt body correctly comes
   from the triage surface (`lib/prompts/surfaces/triage.py`).

Sessions for the same pipeline end up on two different origin values
(`topic-proposal` for drafting, `llm-stage` for review) and drift-triage
sessions are indistinguishable from review sessions once traced, even though
each is a materially different judgment call.

## Confirmed bug (independent of this redesign)

`agent_spawn.py::_drift_is_material` calls:

```python
resolve_proposal_reviewer().complete(_triage_prompt(*inputs), max_tokens=512, cwd=repo_path)
```

with no `surface_id` argument, so it falls back to the reviewer's bound
`REVIEW_SURFACE_ID`. Every drift-triage run is traced as a
`topic-proposal-review` session. The fix is a one-line `surface_id=` override
at the call site — small, low-risk, and independent of whether the broader
unification below ever happens.

## Why full unification is non-trivial

The obvious question is why drafting doesn't just reuse review's
`ExternalAgentLLM.spawn_spec()` + detached-worker pattern, since the two are
already structurally close: both Popen the configured agent, pipe a prompt to
stdin, write output to a known path, and rely on the agent calling back a
`*-finish` CLI command rather than trusting the parent's `communicate()`
timeout. That closeness is a real opportunity, but two differences currently
block a drop-in merge:

- **Blocking vs. detached from the caller's perspective.** Drafting blocks
  the HTTP request thread that started it (by design — the caller needs the
  proposal's `trace_id` and initial status back synchronously); review's
  detached mode returns immediately and reports back only through
  `review-finish` polling. Moving drafting onto the detached-thread pattern
  changes the run's request/response contract, not just its internals.
- **Provider-agnostic agents may not fire regin's hooks.**
  `settings.topic_proposal_external_agents` accepts *any* CLI convention
  (`TopicProposalExternalAgent` — Claude's `--print`, Codex's `exec`, Kimi's
  `-p {prompt}`), not only Claude Code. `REGIN_LLM_SURFACE` → `llm_surface`
  tracing depends on a SessionStart hook firing inside the spawned process.
  Whether that holds for every configured provider (see the
  `agent-providers` topic, `lib/providers/`) rather than only Claude Code is
  unverified — drafting's manual `_emit()` may exist specifically to
  guarantee trace visibility for agents that never fire a regin hook at all.
  This needs a spike, not an assumption, before drafting's manual span
  emission is removed.

## Open questions for whoever picks this up

1. Does `REGIN_LLM_SURFACE`-based hook tracing actually fire for every
   configured `TopicProposalExternalAgent` provider (Claude, Codex, Kimi),
   or only for Claude Code? Check `lib/providers/` and each provider's hook
   install path.
2. If review's detached-worker pattern (`_review_agent_worker`) were
   extracted into a shared helper, what would drafting's callers (which
   currently expect a synchronous return with `trace_id` + initial status)
   need to change to consume it?
3. Should `_stamp_topic_proposal_origins` keep being a bespoke ingest path,
   or can drafting be moved onto `REGIN_LLM_SURFACE` + `_stamp_llm_stage_origins`
   without losing the richer status tracking drafting's manual `_emit()`
   calls currently carry (pid, validation-failure retries)?

## Proposed phasing

- **Phase 0 — bug fix.** Pass `surface_id=` explicitly in the triage
  `.complete()` call so drift-triage stops being traced as a review run.
  Ships independently of everything else here.
- **Phase 1 — tag-stamping parity.** Extend `_stamp_topic_proposal_origins`
  to also apply the drafting surface's declared `PromptSurface.tags` (a
  single, unambiguous surface id — no per-call disambiguation needed),
  mirroring what `_stamp_llm_stage_origins` already does for the other
  surfaces. This does not change drafting's spawn mechanism, only makes its
  prompt-management tags take effect on sessions.
- **Phase 2 — extract the shared spawn primitive.** Factor the
  Popen-plus-finish-signal shape duplicated between
  `run_external_agent_proposal` and `_review_agent_worker` into one helper,
  answering open question 2 above. Lower risk than Phase 3 because it
  doesn't touch tracing.
- **Phase 3 — unify tracing.** Once open question 1 is answered, decide
  whether drafting can drop its manual `_emit()` calls in favor of
  `REGIN_LLM_SURFACE` + the standard hook path, collapsing to one tracing
  mechanism for all three stages. Highest risk: touches the most-used
  pipeline's trace visibility; needs its own build-then-verify loop with
  before/after trace captures on a real proposal run, not just unit tests.

## Out of scope here

Assigning tags to the `prompt_templates` rows that have none
(`grader-correctness`, `grader-process`, `topic-split-leaf`,
`topic-group-buckets`, the three topic-proposal-agent surfaces, and the
non-registry `gitnexus-usage` fragment) is a separate, much smaller task that
was paused pending this design — see the session that produced this doc.
