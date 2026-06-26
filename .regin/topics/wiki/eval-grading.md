# Evaluation & grading

This bucket holds everything in regin that **measures quality or judges good-vs-bad** — turning a finished agent session (or a recall result, or a skill ablation, or a goal outcome) into a verdict instead of a vibe. Four largely independent mechanisms live here:

| Concern | Code | What it judges |
|---|---|---|
| **Session grader** | `lib/grader/` | A completed regin trace, on a correctness axis and a process ("agentic") axis, plus reviewer-defined aspects. |
| **Recall-quality eval** | `lib/memory/evaluate.py` | Whether the memory recall pipeline surfaces the *right* memory for a query (hit@1 / hit@k / MRR). |
| **Pattern-conceal experiments** | `lib/experiments.py` | Whether hiding a `SKILL.md` section changes how often its rules fire (skill ablation A/B). |
| **Goal verification feedback** | `lib/goal_feedback.py` | The outcome of a `/goal-verified` run — which surfaced lessons helped, and which acceptance items failed. |

They share a worldview — *score against evidence, never against the agent's own restatement* — but they are separate code paths with separate entry points. Start at the grader; the other three are smaller and self-contained.

---

## 1. The session grader (`lib/grader/`)

A **post-hoc, LLM-judge rubric grader** for captured Claude Code sessions. It grades a completed trace on **two independent, never-fused axes** (`lib/grader/__init__.py` module docstring; `AXES` in `lib/grader/service.py`):

- **correctness** — three sub-criteria:
  - *groundedness* — every load-bearing claim in the final deliverable is backed by a recorded trace span (`lib/grader/grounding.py`),
  - *coverage* — every required item from the user's task is addressed (`lib/grader/coverage.py`),
  - *source quality* — each grounding source is classified AUTHORITATIVE / PROXY / UNVERIFIED (`lib/grader/source_quality.py`).
- **process** (the "agentic" axis) — `lib/grader/process.py`: P1 tool-use appropriateness, P2 redundancy/thrash, P3 reliability (error recovery), P4 cost-proportionality against a task-class percentile.

The rubric is **data, not prose** (`lib/grader/rubric.py`): `CORRECTNESS_RUBRIC` and `PROCESS_RUBRIC` carry the criteria, verdict vocabularies, pass ratios, and hard gates; `RUBRIC_VERSION` (`"v1"`) is stamped onto every persisted grade so historical grades stay interpretable. Beyond the two builtins, reviewers can add **aspects** — extra named evaluation dimensions whose description text is injected into the deep judge (`GraderConfig.aspects` / `GraderAspect` in `lib/settings.py`, edited via `web/blueprints/grader_config.py`). The memory subsystem registers one such aspect, `InjectedRelated`, to grade topic-route relevance (see *memory-topic-route-feedback*).

### How the pipeline is wired

`grade_session(trace_id, *, axes=AXES, tier="auto", aspects=…)` in `lib/grader/service.py` is the spine:

1. **Validate** the request (axes / tier / aspects).
2. **Build evidence** — `build_evidence(trace_id)` in `lib/grader/evidence.py` fetches the merged trace projection and indexes its tool events (reads, bash, search, fetch, mutations) into an `EvidenceIndex` alongside the prompts and the final deliverable.
3. **Run grades by tier** (`TIERS`):
   - **screen** — mechanical, *no LLM*: claim extraction (`lib/grader/extraction.py`), grounding, coverage, source-quality, and the process assessments, with gates applied in `lib/grader/correctness.py` / `process.py`.
   - **deep** — an agentic external judge (`grade_combined` in `lib/grader/combined_agentic.py`) that handles all axes + aspects in one pass.
   - **auto** — screen first, escalate to deep only when borderline/failing (`grader.auto_escalate`).
4. **Persist** — one row per (trace_id, axis) into the `session_grades` table via `lib/grader/store.py` (`SessionGrade`, self-creating schema).
5. **Post-grade loops** fire from the service: distill flagged sessions into memory lessons (`grader.distill_on_fail`), score whether injected memories helped (`agent_memory.feedback_on_grade`), and feed topic-route relevance verdicts back (`agent_memory.topic_relevance_feedback`).

**The deep judge is an external subprocess, not an in-process model call.** `resolve_judge` / `ExternalAgentJudge` in `lib/grader/adapters.py` shell out to a configured agent (`grader.external_agent`) with the prompt on stdin and a *locked-down* allowlist (`grader.judge_allowed_tools`) of exactly two commands — `regin trace dump` and `regin trace span`. The judge **self-fetches its own evidence**: it pulls a compact catalog with `trace dump --index`, then reads full spans on demand (`lib/grader/dump.py`). Its noisy CLI output is parsed back with `extract_json_object` (`lib/grader/judge_io.py`). This is the "make the judge agentic" design — give it tools to gather evidence rather than baking pre-extracted text into a static prompt.

### Analytics & the grade→memory loop

- `pareto_points()` (`lib/grader/pareto.py`) — cost-per-correct-outcome analytics across graded sessions.
- `aggregate_failure_modes()` (`lib/grader/aggregate.py`, taxonomy in `lib/grader/failure_modes.py`) — consolidates recurring cross-session failure modes into agent memory once they recur past `grader.aggregate_min_sessions`.
- `lib/grader/topic_notify.py` — pushes topic-suppression proposals to the inbox.

### Entry points

- **Library:** `from lib import grader` → `grade_session`, `latest_grades`, `list_grades`, `pareto_points`, `aggregate_failure_modes` (`lib/grader/__init__.py`).
- **CLI:** `regin grade run|show|list|reflect|pareto` (`cli/commands/grader.py`).
- **Web/API:** `web/blueprints/grades.py` (grades), `web/blueprints/grader_config.py` (aspects + per-axis system-prompt overrides editor).
- **Config:** `GraderConfig` in `lib/settings.py` — `enabled`, `external_agent`, `auto_escalate`, `deep_max_claims`, `judge_allowed_tools`, `distill_on_fail`, `aggregate_min_sessions`, `aspects`, `system_prompt_overrides`.

---

## 2. Recall-quality eval (`lib/memory/evaluate.py`)

A lightweight **regression harness for the memory recall pipeline** — no network, no LLM by default. `evaluate_recall(cases, *, store=None, top_k=5, mode="auto")` runs a set of `EvalCase`s (each a `query` plus `expect_any` substrings) through recall and scores the result with standard retrieval metrics on the `EvalReport`: **hit@1**, **hit@k**, and **MRR**. `mode="fts"` forces lexical-only; `mode="auto"` uses dense + rerank when an embedder is available.

- **Entry point:** `regin memory eval <cases.jsonl>` (`cmd_eval` in `cli/commands/memory.py`) — prints verdicts + metrics and **exits 1 when hit@k < 1.0**, so it doubles as a CI regression guard.
- **Storage:** none — returns an in-memory `EvalReport`; the run is logged as a `read` event (`recall_eval_run`).

This is the quick lexical regression check; the deeper *true-inject-path* end-to-end verification (catching silent dense-path degradation) lives under the **recall-eval-verification** topic.

---

## 3. Pattern-conceal experiments (`lib/experiments.py`)

Skill-ablation A/B: hide (conceal) one or more H2/H3 sections of a pattern's `SKILL.md` **before deployment** and measure the effect on rule-firing. At most one experiment is active per pattern.

- `list_sections(slug)` enumerates a guide's headings (skipping fenced code) for the UI; `apply_conceal(body, sections)` is the pure function that strips chosen sections (and their content, up to the next equal-or-higher heading) while leaving code blocks intact.
- CRUD + lifecycle (`create`, `activate`, `deactivate`, `get_active`, `patterns_with_active`) persist to the `Experiment` ORM model (`lib/orm/models/rules.py`).
- **Where it bites:** the skill deployer calls `experiments.get_active(...)` and runs `apply_conceal` on the body before writing the deployed `SKILL.md` (`lib/skills/skill_deployer.py`).
- **Entry point / readout:** `web/blueprints/experiments.py` — lists experiments and rolls up `RuleTrigger` rows (sessions / checks / fired) since the experiment started, so you can see whether concealing a section moved the firing rate. Gated by `settings.enable_experiments`.

The conceptual write-up and aliases for this live under the **pattern-experiments** topic.

---

## 4. Goal verification feedback (`lib/goal_feedback.py`)

The back half of the `/goal-verified` loop. After a goal is preflighted, built, and independently verified, `record_outcome(goal, *, included_ids, offered_ids, failures, tags, topics, trace_id, …)` records two outcome signals into agent memory:

1. **Engagement** — lessons that preflight *offered* and the roadmap actually *included* are **reinforced** (`store.reinforce`); offered-but-unused lessons are left to decay. A precise "did this memory help" signal.
2. **Failure-derived lessons** — each acceptance item that *failed* verification is written as a new `lesson` memory, phrased as a transferable rule, stamped with `FAIL_TAG = "goal-verified-fail"`, tagged by area, and linked to authoritative topic nodes (`source = "goal-feedback"`, resolved against `lib.topics.route`). Topic resolution is deliberately exact (no fuzzy fallback) since these are authoritative writes; no-topic sentinels are an intentional skip, not an error.

Returns an `OutcomeResult` (reinforced / unreinforced / ignored / new_lessons / linked_topics …). **Entry point:** `regin goal feedback <goal> [--included] [--offered] [--fail] [--tag] [--topic] [--trace-id]` (`cmd_goal_feedback` in `cli/commands/goal.py`). It reuses existing memory primitives — no new table.

---

## How these connect (and what's *not* here)

The grader is the hub: it reads `session_spans` (so it depends on **session-trace** capture/merge) and its post-grade hooks feed the memory subsystem — distillation (**memory-distillation-capture**), injection usefulness (**memory-engagement-feedback**), and topic-route relevance via the `InjectedRelated` aspect (**memory-topic-route-feedback**). Recall-quality eval here is the fast lexical check; the end-to-end inject-path verification is **recall-eval-verification**. Conceal experiments judge **patterns-skills** deployment; goal feedback writes into the **agent-memory** engine. This bucket owns the *judging* code; the subsystems it judges keep their own topics.