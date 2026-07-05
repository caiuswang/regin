# Memory capture & distillation

Where memories come from. Two birth paths — one explicit, one implicit — both
gated so the store never fills with session-narrating noise.

## Explicit: the lesson tee

`send_to_user(type=lesson)` is teed into the store by
`hook_manager/handlers/post_tool_trace._remember_lesson`, carrying span / agent /
scope provenance and honoring a `supersedes` id (the non-destructive
correction-in-place). This is the deliberate "a future session should know this"
path.

## Implicit: the agentic distiller (`lib/memory/distill.py`)

`distill_session` proposes memories from one *finished* session. It reads the
session's trace out of the **main** regin DB (`session_spans`, PENDING rows
excluded, via `_session_spans`) and resolves the session's own `repo:<name>`
scope through `session_repos` (`_session_scope`). Proposals land with
`status='proposed'` — a human approves them before they participate in recall.
That gate is what keeps the store curated; distill never writes silently.

**Distillation is LLM-only by contract:** the model is what turns a session into
an *abstracted* rule. Deterministic heuristics still run, but only to surface the
highest-signal moments at the top of the LLM input — they no longer fabricate
proposals of their own (that produced "running account" noise). With no
`LLMProvider` configured, distill proposes nothing.

**It is agentic.** `resolve_distiller` (see **agent-memory-architecture**) grants
the read-only `trace dump` / `trace span` tools; rather than folding the whole
session into the prompt, `_compose_prompt` (via `render_surface` on the editable
`memory-distill` surface) hands the model the trace id plus two high-signal hint
blocks and lets it self-fetch only the spans it needs — so prompt size stays
constant regardless of session length, the same scaling fix
`lib/grader/agentic.py` uses. The two hint blocks:

- `_grade_digest` — the automated grader's flagged problems across the
  correctness axis (`_ungrounded_lines` / `_coverage_lines` / `_source_lines`)
  and process axis (`_process_problems`), rendered as "capture the durable rule
  that would have prevented each".
- `_signal_digest` — heuristic notable signals: failure→fix chains
  (`_failure_fix_chains`, same tool + target, fix strictly after failure) and
  user pushback (`_correction_prompts`, matching `_CORRECTION_MARKERS`).

## The importance band and write gates

Each LLM-drafted proposal is schema-validated (`_validated_proposal`: body ≥ 60
chars, a rule-shaped title ≥ 10 chars — the required non-trivial title forces the
model to state the rule, not dump an untitled account; capped at
`_MAX_PROPOSALS = 10`). `_llm_proposals` treats a parsed-but-empty array as an
affirmative "nothing worth keeping".

`_store_proposal` then applies the gates:

- **Importance band** (`_finalize_status`): below `distill_min_importance` →
  dropped; at/above `auto_approve_importance` → auto-approved to `active`; else
  queued `proposed`. A grader-flagged session adds an `importance_bonus` that
  nudges its drafts toward auto-approval — the grade→memory loop's entry point.
- **Dedup-at-write** (`_dedup_candidate` → `_reinforce_existing`): FTS recall
  (no embedder needed) confirmed by `_text_similarity ≥ dedup_text_threshold`
  bumps the existing row's importance instead of writing a near-duplicate.
- **Contradiction-at-write** (`_supersede_candidate`, gated by
  `distill_supersede_on_conflict`): candidates in the lexical gray band
  `[_SUPERSEDE_SIM_FLOOR = 0.5, dedup_threshold)` are put to the LLM
  (`_llm_says_supersedes`, at most `_SUPERSEDE_MAX_CHECKS` per proposal); a
  CONTRADICT retires the old row (`status=retired`, `veracity=false`,
  `superseded_by`). Never retires on a guess.
- **Auto-file** (`_link_meta_root`, gated by `distill_link_meta_roots`): a
  `preference` / `procedure` memory is filed under its meta-root
  (`_KIND_META_ROOT`) so it has a navigable home without waiting for the
  agentic `link-topics` classifier.

Every written proposal is tagged `DISTILL_TAG` + `"llm"`. The idempotency guard
(`distilled_memories_from_trace > 0`) skips re-invoking the LLM for an
already-distilled trace; the `DISTILL_TAG` marker is what distinguishes a distill
row from a `send_to_user(type=lesson)` capture, which also stamps
`source_trace_id`. `force=True` bypasses the guard. Distilled rows are curated
further by **memory-consolidation-reflect** and reviewed at the surfaces in
**memory-curation-surfaces**.