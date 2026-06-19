# Configurable grader: aspects, editable judge prompts, provider selection

Design for making the post-hoc session grader (`lib/grader`) configurable
without dismantling its grounded two-axis machinery. Scope confirmed with the
user: **configure the deep judge** (do not turn every aspect into a new
grounded axis) and a **full Grades UI redesign**.

## Current state (researched)

- Two hard-coded axes — `correctness`, `process` — each with its own grounding
  pipeline. `AXES`/`TIERS` live in `lib/grader/service.py`.
- Rubric bars are rubric-data in `lib/grader/rubric.py` (versioned `v1`),
  deliberately *not* deployment config.
- The deep tier shells out to an **external agent judge**
  (`lib/grader/adapters.py: ExternalAgentJudge` / `resolve_judge`). The agent is
  one of `settings.topic_proposal_external_agents`, picked by
  `settings.grader.external_agent` (None → first configured). Tools are granted
  by appending `--allowedTools <csv>` (a `claude --print` / `codex` convention).
- The judge **system prompts** are Python string constants: `_PROMPT` in
  `agentic.py` (correctness) and `process_agentic.py` (process). Not overridable.
- Grades persist to `session_grades`; the web surface is `GradesView.vue` +
  `GradeReportCard.vue` (a raw `<pre>` of the report text). The "prompt tab" is
  `PromptTemplatesView.vue` (`/prompt-templates`), backed by the
  `prompt_templates` table — today it manages topic-proposal fragments only.
- Providers: `lib/providers/registry.py` models *deploy targets*
  (claude/codex/generic/kimi). The grader's "provider" is the *judge agent*
  (`topic_proposal_external_agents`). `kimi` CLI is installed but is **not** a
  configured judge agent, and its CLI contract differs (prompt via `-p <arg>`
  not stdin; no `--allowedTools`; noisy text output).

## Design

### 1. Aspects (configure the deep judge)

New `GraderAspect` pydantic model on `settings.grader.aspects`:

```
key: str            # stable id
label: str          # display
description: str    # rubric text woven into the deep judge prompt
enabled: bool       # toggled in UI
builtin: bool       # correctness/process; toggle-only, never deletable
```

Defaults seed the two builtins plus a few researched-but-disabled aspects
(completeness, clarity, safety, efficiency — see Research below). Enabled
aspects are rendered into an `<aspects>` block appended to the deep judge
prompt so the judge weighs them; the correctness/process grounding pipelines
are untouched. User-added aspects are judged against their description by the
same self-fetching judge — no new grounded axis, no schema change.

### 2. Editable judge system prompts

New resolver `lib/grader/prompts.py`:

```
judge_system_prompt(axis, default) ->
    (settings.grader.system_prompt_overrides.get(axis) or default)
    + rendered <aspects> block
```

`agentic.py` / `process_agentic.py` call the resolver instead of using the bare
constant. Overrides persist in `settings.grader.system_prompt_overrides`
(`{axis: text}`); empty/blank → fall back to the built-in default. The prompt
tab gains a "Grader prompts" section to edit + reset these.

### 3. Provider selection

- `grade_session(..., provider=None)` threads an explicit judge agent id down
  through `_resolve_judge` → `resolve_judge(agent_id=...)` →
  `ExternalAgentJudge(agent_id=...)`, overriding `settings.grader.external_agent`
  for that run. Persisted default stays `settings.grader.external_agent`.
- `POST /api/sessions/<id>/grade` accepts an optional `provider`.
- The redesigned Grades view exposes a judge-provider dropdown (the configured
  judge agents) and persists the default via the grader-config endpoint.

### 4. Judge provider parity (so Kimi can actually judge)

Extend `TopicProposalExternalAgent`:
- `{prompt}` placeholder in `args` → the prompt is substituted there instead of
  piped on stdin (Kimi: `args: ["-p", "{prompt}"]`). No placeholder → current
  stdin behavior (Claude/Codex unchanged).
- `supports_allowed_tools: bool = True` → when False, `resolve_judge` does not
  append `--allowedTools` (Kimi has no such flag).

Robust JSON extraction in the judge parsers: take the **last** balanced
`{...}` object, so a wrapper line like Kimi's `{"suppressOutput": true}` hook
echo doesn't corrupt the parse.

### 5. Config API

`web/blueprints/grader_config.py`:
- `GET /api/grader/config` → `{ aspects, system_prompts (with defaults),
  judge_providers (configured agent keys), external_agent, tiers }`.
- `PUT /api/grader/config` → persist aspects / system_prompt_overrides /
  external_agent (merged onto the existing persisted `grader` block).

### 6. Grades UI redesign

Rework `GradesView.vue` + `GradeReportCard.vue`: summary header, per-session
rows with verdict pills, an expandable structured report (scoreboard + bullets
parsed from the grade, not a raw `<pre>`), tier/judge/provider chips, filters,
and a "Grader settings" panel (aspect toggles/add + judge-provider select). The
prompt tab gets the grader-prompt editor.

## Research: evaluation aspects

Common LLM-as-judge / eval-system dimensions (G-Eval, RAGAS, OpenAI evals,
Anthropic eval guidance): **correctness/faithfulness**, **completeness/
coverage**, **groundedness/citation**, **clarity/readability**, **safety/
harmlessness**, **efficiency/cost**, **relevance**, **style/conventions**.
regin's two axes already cover correctness (groundedness+coverage+source) and
process (efficiency+reliability). We seed the gap-fillers — completeness,
clarity, safety, efficiency — as **disabled** optional aspects the user can
turn on, keeping the default behavior identical.

## Incremental commits

1. This design doc.
2. Backend: `GraderAspect` + config fields + `prompts.py` resolver + weave +
   provider threading. Unit tests.
3. Backend: judge provider parity (`{prompt}` / `supports_allowed_tools` /
   robust parse) + Kimi judge config. Unit tests.
4. `GET/PUT /api/grader/config` + `provider` param on grade. Tests.
5. Frontend: prompt tab grader-prompt editor.
6. Frontend: Grades UI redesign + aspects/provider config.
7. Kimi end-to-end verification.

## Non-goals

- New grounded axes per custom aspect (explicitly out, per the design choice).
- Moving rubric *bars* into deployment config (they version with the rubric).
