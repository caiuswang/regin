# Loop-engineering: goal preflight → verified build → feedback (`goal-verified-loop`)

The two regin-side halves of the `/goal-verified` workflow. The idea: pin a *falsifiable* bar **before** the agent builds, so verification checks against something concrete instead of the agent grading its own homework. The build + verify halves themselves live in `.claude/skills/goal-verified/SKILL.md` (cited as docs); only preflight and feedback are code in this repo.

> **Refreshed 2026-06-27.** The preflight area-router (`AREA_RULES` / `AreaRule` / `detect_areas` / `resolve_references`) was **retired** in `7ee1ebd`. It routed off goal *prose* by literal token/keyword matching, so it went hollow for any goal phrased as intent, and the table merely restated the file-keyed convention table in `CLAUDE.local.md`. Preflight is now a small portable kernel: **hard gates + opt-in recalled lessons**, nothing area-specific. Any earlier wiki text describing skills/reference-components/design-tokens routing is obsolete.

## Preflight — the front half (`lib/goal_preflight.py`)

A freeform goal becomes a small `Roadmap` dataclass with three fields only: `{goal, gates, lessons}`.

**Hard gates are a fixed universal floor — not routed.** `BASE_GATES` is two items every run must pass regardless of area: *the existing test suite stays green*, and *an independent fresh-context reviewer checked the diff (`/code-review high`)*. The kernel never invents an area standard. Per-area machine gates (pytest / radon / vite / playwright) come from the file-keyed convention table in `CLAUDE.local.md`, read by the consuming skill before editing — not from this module. `build_roadmap(goal)` is deterministic and offline.

**Lessons are opt-in and demoted.** `build_roadmap(goal, with_lessons=True)` calls `recall_lessons`, which runs the shared `lib.memory` store in **FTS mode** (lexical BM25 on the goal text, pre-code, no dense-model load) with `reinforce=False` — merely *offering* a lesson is not *using* it, so it must not bump `recall_count` (the very usefulness signal feedback measures). This flat leg is **off by default at the CLI** as of 2026-06 (it measured ~22% injection engagement); structure-first recall via `regin memory recall-for-task` (importance-ranked subsystem subtree, not text similarity) is now preferred, and `--with-lessons` is kept only to A/B the old leg.

**The engagement denominator is automatic.** `record_offered(session_id, lessons, goal)` logs the offered lessons through the store's `record_injections` (exposure, *not* usefulness — no `recall_count` bump), so reflect's decay half can later see "offered many times, never used → fade" even when a run never calls `goal feedback`. It needs `--session-id` (a CLI subprocess has none in its env) and degrades to a no-op otherwise.

**The one generative step is deliberately left to the consumer.** `render_markdown` emits the lessons menu + hard gates, then leaves the **acceptance checklist** as a prompt: turning a fuzzy goal into 3–8 concrete, falsifiable items (states, counts, 0/1/N edge cases) is the irreducibly generative step and belongs to the agent that consumes the roadmap. `roadmap_warning` now flags exactly one degenerate case — an empty goal — because with routing gone there is nothing else for a roadmap to be "hollow" about.

**CLI** (`cli/commands/goal.py`): `regin goal preflight "<goal>"` with `--json`, `--with-lessons/--no-lessons` (off by default), and `--session-id`. The legacy `--repo-root` was dropped with the router. The command logs `gate_count` / `lesson_count` / `offered_recorded` to the `goal` activity log.

## Feedback — the back half (`lib/goal_feedback.py`)

After a verified run, `record_outcome` folds the outcome back into the **existing** memory store — reusing `remember` / `reinforce` only, adding no table or index of its own:

1. **Engagement, high-precision.** A lesson the agent folded into the *approved* acceptance checklist (`--included`) is reinforced; an offered-but-not-included id (`--offered`) is recorded as ignored and left for reflect's `_decay_chronically_ignored`. Human approval is a far cleaner "did this help" verdict than the trace-referent heuristic in `lib.memory.feedback`. `_reinforce_all` returns matched vs missed so a bad/ambiguous 8-char prefix id is reported, not silently counted as success.
2. **New lessons from failures.** Each failed acceptance item (`--fail`, phrased as a transferable *rule* by the skill, not an episode) is written as a `lesson` memory tagged by area plus `FAIL_TAG` (`goal-verified-fail`), so the next roadmap recalls it.
3. **Authoritative topic filing.** `--topic <node-id>` links each new failure-lesson under topic nodes the agent already knows from its tree walk. Resolution is exact-id-or-slashed-leaf with **no fuzzy keyword fallback** — this is an authoritative write, and a wrong link is worse than none (`match_topic` confidently misroutes on a single coincidental token). A `none` / `-` sentinel files the lesson unbound on purpose.

**CLI**: `regin goal feedback "<goal>" --included … --offered … --fail … --tag … --topic … --trace-id …`, `--json` for the machine view.

## Cross-refs

- The engagement/decay coupling is [memory-engagement-feedback](./memory-engagement-feedback.md); the recall leg rides [memory-recall-pipeline](./memory-recall-pipeline.md), and the structure-first replacement is the memory topic-tree walk.
- The universal gate floor mirrors the conventions enforced by [rule-engine-design](./rule-engine-design.md).
- The `--session-id` / `--trace-id` that preflight and feedback consume come from [session-id-probe](./session-id-probe.md).
