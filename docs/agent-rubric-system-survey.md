# Survey: An Agent Rubric / Outcome-Grader System for Grading Claude Code Sessions

*Survey stage — design principles, landscape comparison, and recommendations. No implementation.*
*Compiled 2026-06-12. Reliability figures cited below reflect the model generations the source papers tested; treat magnitudes as dated and directions as durable.*

## Purpose

Inform the design of a rubric/grader system that **post-hoc grades Claude Code sessions** (captured as regin
traces) on two axes:

1. **Factual / citation correctness** of the agent's outputs.
2. **Process / efficiency** of how the agent got there.

Anchored on Anthropic's "Verify with Outcome Grader" cookbook (managed agents / Outcomes), broadened into a
comparative survey of the LLM-as-judge / rubric-based / agent-evaluation landscape.

## 1. Thesis

The literature converges on one shape: a **multi-criterion, per-dimension, context-isolated rubric grader that
is outcome-anchored but trajectory-aware**, with hidden rubrics, mandatory per-criterion evidence, baked-in bias
mitigations, and an explicit token/cost-aware process score. The cookbook is one instance of the more general
**evaluator-optimizer** architecture [1]; for regin's *post-hoc* grading the iterative loop degenerates to a
single scoring pass, but the rubric-design discipline carries over intact.

The high-leverage, non-obvious fact: because regin already captures the **full trajectory + workspace + cost
data**, it is positioned to use the strongest known agent-evaluation method — a trajectory-reading "agentic"
grader — cheaply. That is the exact thing that lifts agent grading from ~60% to ~90% human alignment [9].

## 2. The anchor pattern, generalized

The cookbook's writer↔grader loop is a special case of the **evaluator-optimizer workflow**: "one LLM call
generates a response while another provides evaluation and feedback in a loop," most effective "when we have
clear evaluation criteria, and when iterative refinement provides measurable value" [1]. Two adaptations for
regin:

- **Grading is after the fact, not in the loop.** regin scores a *completed* session, so the loop collapses to
  a single grading pass [1]. The refinement machinery (max_iterations, feedback-to-writer) re-engages only if
  grades later *trigger re-runs* or rank candidate sessions.
- **The rubric is still the entire product.** Every reliability and anti-gaming property below is a property of
  the rubric and the grader's isolation, not the loop.

## 3. Landscape map — families of evaluation approaches

| Family | Grades | Output | Strength | Weakness | Fit for regin |
|---|---|---|---|---|---|
| **Outcome grader / evaluator-optimizer** (Anthropic Outcomes) [1] | Final artifact vs hidden rubric | per-criterion satisfied / needs_revision | Independent, hard to con; rubric is explicit | Needs clear criteria; loop wasted post-hoc | **Core skeleton** |
| **LLM-as-judge, pointwise** (G-Eval) [2] | One output, per criterion | numeric / form-filled score | CoT + form-filling; 0.514 Spearman vs humans on summarization [2] | Lenient default (7–8/10); position/verbosity bias [3][4] | **Primary scoring mode** |
| **LLM-as-judge, pairwise** (MT-Bench, PairS) [3] | A vs B | preference | Aligns with humans better than absolute scoring [5] | O(N²); position-bias-prone; needs two sessions | Optional, for run-vs-run ranking |
| **Agent-as-a-Judge** [9] | The *trajectory + workspace* | per-requirement verdict | ~88–90% human alignment vs ~60% for plain judge; ~97% cheaper than humans [9][10] | Validated on small set (DevAI, n=55); more tokens | **Best fit for process axis** |
| **Reference-based factuality** (FActScore, ALCE) [a][b] | Decomposed claims + citations | support rate, citation precision/recall | Claim-level granularity; well-established | Needs sources/gold; decomposition cost | **Correctness axis tooling** |
| **PRM vs ORM** (process vs outcome reward) | Each step vs final answer | scalar reward | Step-level catches *where* it went wrong | Rigid step-checks are brittle [11] | Inform, don't hard-code steps |
| **Cost-controlled benchmarks** (Kapoor Pareto, HAL) [12][13] | Accuracy *and* cost | Pareto frontier | Treats cost as first-class | Benchmark-level, not per-session rubric | **Justifies the efficiency axis** |

## 4. Axis 1 — Factual / citation correctness

The strongest ready-made schema is **Anthropic's research-agent triad**, which maps 1:1 onto the correctness
axis [11]:

- **Groundedness** — "claims are supported by retrieved sources."
- **Coverage** — "key facts a good answer must include."
- **Source quality** — "the consulted sources are authoritative, rather than simply the first retrieved." [11]

The source-quality criterion matters because judges suffer a **concreteness / source bias** — they reward
authoritative-*looking* citations regardless of correctness [6]. So the grader must **verify grounding, not
reward citation presence**. (A neighboring "authority bias" claim — that injecting an *incorrect* authoritative
reference tanks scores more than verbosity — was **refuted 0-3** in verification; do not build on it. The
supported finding is the concreteness bias, which cuts the other way.)

Judges **can** detect injected factual errors when asked to: pointwise scores drop sharply on error injection
(Claude Sonnet 4.5: 8.96 → 3.22; GPT-5.1: 9.20 → 3.38) [7]. That detection capability is what the correctness
axis relies on.

Granular tooling from the source sweep:

- **FActScore** [a] — decompose an answer into atomic facts, score the fraction supported by a source. Good for
  "did the agent's summary hallucinate?"
- **ALCE** [b] — automatic **citation precision/recall**: does each citation support its sentence, and is every
  claim cited? Directly applicable to grading whether a session's claims ("this function does X") are grounded
  in files it actually read.

The cookbook's own citation check (LIVE → QUOTE_MATCH → SUPPORTS_CLAIM) is a concrete, mechanically-checkable
instance of ALCE-style grading.

## 5. Axis 2 — Process / efficiency

**Grade the outcome, not a rigid path — but keep a process grader alongside.** Anthropic is explicit: "it is
often better to grade what the agent produced, not the path it took"; checking a specific tool-call sequence is
"too rigid and results in overly brittle tests" because agents find valid unanticipated approaches [11]. Yet
they *also* grade transcripts in addition — precisely the two-axis split. The process axis should grade
**properties** of the trajectory (tool-use appropriateness, redundant looping, cost proportional to task
difficulty), not adherence to a prescribed step list. This is the PRM-vs-ORM lesson without the brittleness:
outcome-anchored, trajectory-*aware*.

**Use an agentic evaluator for this axis.** An evaluator that inspects the trajectory/workspace
(Agent-as-a-Judge) reaches ~88–90% alignment with human consensus on code-agent evaluation (OpenHands 90.44%)
versus ~60% for a plain LLM judge (60.38%) [9] — at ~97% lower cost/time than human review (~$30 / ~2 hrs vs an
estimated 86.5-hr human baseline) [10]. regin captures full traces, so this method is *native* to its data.
Caveat: validated on DevAI (n=55), which favors agentic evaluators; generalization to regin's mixed sessions is
untested.

**Cost is a first-class axis, not a footnote.** "Agentic systems often trade latency and cost for better task
performance" [1]; the benchmark literature argues agent evaluation must be **cost-controlled**, plotting
accuracy–cost Pareto frontiers (Kapoor et al. [12]), and HAL confirms accuracy gains carry disproportionate
cost [13]. This backs regin's distinctive angle: it already separates cache-read / write / output tokens and
per-tool cost, so a process score weighing *task success against tokens-and-steps spent* is something regin can
compute that generic graders cannot.

## 6. The judge-reliability problem

A rubric grader is only as trustworthy as the judge underneath it. Several biases apply to **rubric/pointwise**
scoring, not just pairwise:

| Bias | Effect | Mitigation that transfers to the rubric |
|---|---|---|
| **Position** | Verdicts flip on order-swap; "rubric-based evaluation implicitly resembles a multi-choice setting" so judges favor score-options at certain list positions [3][4] | Order-swap and **count a pass only if it wins both ways** [3]; **balanced-permutation calibration** (5 forward + 5 reverse rotations) improved Spearman ~+0.03 [4] |
| **Smaller-model amplification** | Qwen3-8B picks position 1 ~30–39% (vs 20% baseline); GPT-4.1 near-uniform [4] | **Use a strong judge model** — biases shrink with capability |
| **Verbosity** | Judges favor longer answers with no new info (GPT-4 fooled 8.7%, weaker models ~91%) [3] | **Penalize unsupported verbosity** explicitly |
| **Self-preference / source** | Judges favor LLM-generated and authoritative-looking text [2][6] | Context isolation, hidden rubric, **require evidence not assertion** |
| **Leniency / prompt-detail** | Judges default to 7–8/10; a detailed structured rubric yields harsher, more discriminating scores than a minimal one [7] (2-1, prompt-based judges) | **Write detailed, structured rubrics**; few-shot anchors (raised GPT-4 consistency 65%→77.5% [3]); reference-guided grading [3] |

Reliability ceiling: strong judges reach >80% human agreement on non-tie cases (MT-Bench S2: 85% vs 81%
inter-human) [8], and G-Eval's 0.514 Spearman is a realistic *upper bound* for pointwise correlation on
subjective dimensions [2] — enough to **anchor and triage**, not to be ground truth. Calibrate against periodic
human spot-checks.

## 7. Rubric design principles (the transferable core)

1. **Decompose — one isolated judge per dimension**, not one judge scoring everything; a single big call
   "dilutes focus and yields unstable scores" [11][2]. (Tradeoff: more calls/tokens.)
2. **Make each criterion mechanically checkable** — tests, file:line references, fetched evidence; verifiable
   signals beat subjective ones [1][11].
3. **Force the grader to earn `satisfied`** with concrete evidence; specify the bar quantitatively.
4. **Anticipate shortcuts / design against gaming** — passing must "genuinely require solving the problem" [11].
   Post-hoc grading lowers gaming risk, but it returns the moment grades feed selection or training.
5. **Hide the rubric from the writer**; keep the grader context-isolated with no memory of prior loops.
6. **Mandate a strict feedback format** — one-line scoreboard + one bullet per failure (what's wrong + what to do).
7. **Tell the grader what to ignore** to prevent thrashing on style nits and pre-existing issues.

## 8. Recommendations for regin (architecture sketch, not code)

**A. Two independent rubric tracks, scored separately, never collapsed into one number.** Keep a `correctness`
grade and a `process/efficiency` grade as distinct outputs. The literature gives no good way to fuse them
without one swamping the other (§9); surfacing both also fits regin's observability ethos.

**B. Correctness track = the groundedness / coverage / source-quality triad** [11], implemented as per-criterion
pointwise judges (G-Eval-style CoT + form-filling [2]), with ALCE-style citation precision/recall [b] and
FActScore-style claim support [a] as the checkable sub-criteria. The grader re-verifies grounding against the
files/sources the session actually touched (which regin's trace records), not the mere presence of a citation.

**C. Process track = an agentic, trajectory-reading grader** [9] over regin's captured spans, scoring
*properties* not prescribed steps: tool-call appropriateness, redundant-loop / backtrack detection, and a
**cost-proportionality** criterion that uses regin's existing token/cost breakdown.

**D. Bake in bias mitigations from day one:** strong judge model; hidden, detailed/structured rubrics; mandatory
evidence; order-swap or balanced-permutation calibration for multi-option scoring [3][4]; explicit verbosity
penalty; reference-guided grading where a gold session exists.

**E. Two-tier cost strategy:** a cheap pointwise judge screens all sessions; reserve the expensive agentic
trajectory grader for borderline/failing ones.

**F. Treat grades as triage, not truth.** With a realistic ceiling around 0.5 Spearman / ~80% agreement [2][8],
the system surfaces suspicious sessions and explains *why*; keep a human-spot-check loop for calibration.

## 9. Open questions the literature does *not* settle

- **Trajectory length.** Judge-reliability numbers come from short single-turn NLG/QA outputs; whether they hold
  over long multi-step Claude Code traces is **unmeasured**.
- **Score aggregation across axes.** Little guidance on weighting/gating or combining an efficiency penalty with
  a correctness score without one dominating.
- **Capturing pairwise's alignment edge in a pointwise setting** — whether reference-guided pointwise or
  A/B-against-a-gold-session recovers it.
- **When the expensive agentic grader is worth it** vs cheap screening — unproven for regin's mixed sessions.

## 10. Reliability notes on this survey

- **Time-sensitivity:** MT-Bench absolute numbers are from earlier model generations — directions persist,
  magnitudes are optimistic/stale.
- **Split votes (directional):** judge-as-anchor agreement (2-1, excludes ties), pairwise > pointwise (2-1,
  relayed via survey), prompt-detail strictness (2-1, prompt-based judges only).
- **One refuted claim (don't use):** "authority bias drops scores more than verbosity" was refuted 0-3 — the
  supported finding is concreteness bias, which says verify grounding.
- **Anchor concentration:** eight findings rest on two Anthropic engineering posts — authoritative for *what
  Anthropic recommends*, but practitioner guidance, not quantitative reliability proof.

## 11. Correctness-triad rubric schema (concrete)

A rubric specification + rubric-as-data schema for grading the **correctness axis** against a regin trace.
Design artifact, not implementation.

### 11.0 Core re-framing: "claim ↔ span"

A Claude Code session has no Sources section, but regin records the equivalent: **every `tool_use` span (Read,
Bash, Grep, WebFetch, …) and its output is a citable source.** The grader's unit of work:

> For every **assertion the agent made**, find the **trace span that backs it** — and judge the link, not the assertion.

- **Artifact** = the agent's assertions (assistant messages, final summary, code comments, the diff's claimed effect).
- **Citations** = `(claim_id → span_id)` links the grader reconstructs from the trace.
- The grader has the same toolset the writer had — it can re-Read a file or re-run a check to verify, exactly as
  the cookbook grader re-fetches URLs.

### 11.1 Step 0 — build the claim ledger

The grader first extracts every checkable assertion into a typed ledger; the **type** determines which span can
ground it:

| Claim type | Example assertion | Grounding span class |
|---|---|---|
| `state` | "`parse()` strips the prefix before validating" | `Read` span containing those lines |
| `result` | "all tests pass" / "the build is green" | `Bash` span: command + exit code + stdout |
| `external` | "the SDK's `retry` defaults to 3" | `WebFetch` of an authoritative source |
| `diagnostic` | "the bug is the unawaited promise in `login()`" | a Read showing it **and** a repro/result span |

Non-checkable text (hedges, plans) is out of scope (§11.10).

### 11.2 Criterion G — GROUNDEDNESS

> Every assertion is backed by a span that actually supports it.

- **Unit:** each claim. **Verdict:** `GROUNDED` · `UNGROUNDED` · `CONTRADICTED` · `STALE`.
- **Bar by type:** `state` → Read/Grep span whose recorded content contains the cited lines (cite `file:line`
  from the *span output*, not the agent's restatement); `result` → Bash span whose command matches and whose
  exit/stdout confirms ("tests pass" with no run ⇒ `UNGROUNDED`; exit≠0 ⇒ `CONTRADICTED`); `external` → WebFetch
  span with QUOTE_MATCH + SUPPORTS_CLAIM; `diagnostic` → **both** a cause span and an effect/repro span.
- **`STALE` rule (regin-specific):** evidence span exists but a *later* span mutated the cited target after the
  read (order spans by timestamp). Generic graders can't see this; regin has the timeline.
- **Evidence required:** `span_id` + `file:line`/`command` + one-line why-it-supports. No evidence ⇒ cannot mark
  `GROUNDED`.
- **Anti-gaming:** never accept the agent's paraphrase of a tool result as evidence — require the actual span output.

### 11.3 Criterion C — COVERAGE

> Every key fact / sub-task a correct answer must include is addressed.

- **Unit:** a **required-items checklist** derived from the user's task, fixed *before* grading (more specific
  than the task, per the cookbook). E.g. "fix the login 500 and add a regression test" → `{root cause
  identified, fix applied, regression test added, test exercises the bug, full suite green, no unrelated files
  touched}`.
- **Verdict:** `COVERED` · `PARTIAL` · `MISSING`.
- **Bar:** each item must be present *and* `GROUNDED` (coverage piggybacks on G). **Anti-gaming:** the checklist
  is fixed before reading the session, so the agent can't define coverage down.

### 11.4 Criterion S — SOURCE QUALITY

> The agent relied on authoritative sources, not convenient proxies. Defends against concreteness bias [6].

- **Unit:** each distinct source backing a `GROUNDED` claim. **Verdict:** `AUTHORITATIVE` · `PROXY` · `UNVERIFIED`.

| Claim about… | AUTHORITATIVE | PROXY (downgrade) |
|---|---|---|
| current repo behavior | the file at HEAD (`Read` of the real path) | a comment, a README, model prior knowledge |
| a library/API's behavior | library source or official docs via `WebFetch` | a Stack Overflow / blog snippet, memory w/o fetch |
| a runtime result | a `Bash` span that ran it | "this should pass" with no run |
| an external fact/version | official-domain fetch | search snippet, mirror, reposter |

- **Bar:** a claim backed only by `PROXY` caps the criterion at `needs_revision` with a specific reason;
  `UNVERIFIED` (source named but no span proves consultation) is a hard miss.
- **Anti-gaming:** mirrors the cookbook's "do NOT corroborate via mirrors/reposts/snippets."

### 11.5 Scoring, gating & aggregation

- **Scoreboard:** `Groundedness G/N · Coverage C/K · Source-quality A/M`.
- **Gates (first):** any `CONTRADICTED` claim ⇒ correctness **FAIL**; any `MISSING` required item ⇒ at most
  `needs_revision`.
- **Graded remainder:** pass when each ratio clears its configured bar (Groundedness typically 1.0). Report
  ratios regardless for trending.
- **Do not fuse with the process axis** (§8A).

### 11.6 Mandated output format

```
Line 1:  Groundedness G/N. Coverage C/K. Source-quality A/M.  Verdict: <satisfied|needs_revision|fail>
Then one bullet per UNGROUNDED/CONTRADICTED/STALE claim:  "[claim-id] <type> — <bar it failed>. <what's missing/contradicts>."
Then one bullet per MISSING/PARTIAL coverage item:        "[item] — <not addressed | partial: what's absent>."
Then one bullet per PROXY/UNVERIFIED source:              "[claim-id] <source> — <why it's a proxy + the authoritative source to use>."
One sentence max per bullet.
```

### 11.7 Rubric-as-data schema

```yaml
rubric:
  axis: correctness
  applies_to: { task_type: "bugfix" }
  judge: { model: <strong-model>, isolation: fresh_context, sees: [artifact, trace, rubric] }
  criteria:
    - id: groundedness
      unit: claim
      verdicts: [GROUNDED, UNGROUNDED, CONTRADICTED, STALE]
      evidence_required: true
      pass_bar: "all claims GROUNDED; zero CONTRADICTED"
      gate: { on: CONTRADICTED, result: fail }
      grounding_map:
        state:      { span: [Read, Grep], check: lines_present }
        result:     { span: [Bash], check: exit_code_and_stdout }
        external:   { span: [WebFetch], check: quote_match_and_supports }
        diagnostic: { span: [Read, Bash], check: cause_and_effect_both }
      stale_rule: "later mutating span on cited target ⇒ STALE"
    - id: coverage
      unit: required_item
      checklist_source: derive_from_task
      verdicts: [COVERED, PARTIAL, MISSING]
      pass_bar: "ratio >= 0.9; zero MISSING gates to needs_revision"
      depends_on: groundedness
    - id: source_quality
      unit: source
      verdicts: [AUTHORITATIVE, PROXY, UNVERIFIED]
      taxonomy_ref: coding_source_authority
      pass_bar: "ratio >= threshold; no UNVERIFIED on load-bearing claims"
  bias_mitigations: { multi_option_scoring: order_swap_or_balanced_permutation, require_evidence_not_assertion: true, verbosity_penalty: true }
  ignore: [hedges, plans, pre_existing_issues, style_nits]
  output_format: scoreboard_then_failure_bullets
```

### 11.8 Worked example

Task: "Fix the login 500 and add a regression test." Agent summary: *"Root cause was an unawaited
`verifyToken()` in `login()`. Added `await`, added a regression test in `auth.test.ts`, the full suite passes."*

```
Groundedness 3/4. Coverage 3/4. Source-quality 2/2. Verdict: needs_revision

- [c4] result — "the full suite passes" UNGROUNDED: no Bash span runs the suite after the edit;
  last test run (span#31) predates the Edit (span#37). Re-run before claiming green.
- [item: regression test exercises the bug] — PARTIAL: auth.test.ts added (span#37) but no span
  shows it failing pre-fix, so it isn't proven to cover the 500.
```

The "all tests pass" claim is caught as `STALE`/`UNGROUNDED` because the test run *predates* the fix — visible
only because regin timestamps spans.

### 11.9 / 11.10 Bias mitigations & ignore list

- **Concreteness bias [6]** is the headline risk, neutralized by Source-Quality + "verify grounding, not
  citation presence" (the grader opens the span, never trusts the citation). **Leniency [7]** countered by the
  detailed structured rubric. **Position bias [3][4]** only bites graded *levels* — the triad's enum verdicts
  sidestep it; apply order-swap if a graded sub-score is added. **Self-preference [2]** countered by context
  isolation + same-toolset re-verification.
- **Ignore:** hedges/plans/narration, pre-existing issues not introduced this session, style/formatting, and the
  agent's *reasoning* about why it acted (grade the assertion, not the rationale).

## 12. Process / efficiency rubric schema (concrete)

A rubric specification + rubric-as-data schema for grading the **process axis**. Outcome-anchored but
trajectory-aware — it grades *properties* of the trajectory, never a prescribed step sequence (rigid step-checks
are brittle [11]).

### 12.0 Framing — the rule that keeps this axis honest

The process grader reads the **full span timeline** (the Agent-as-a-Judge posture that buys ~90% vs ~60%
alignment [9]) and scores *how* the agent worked. But efficiency is only meaningful **conditioned on the
correctness verdict**:

> A cheap session that produced wrong output isn't efficient — it's *cheaply wrong*. A standalone "low cost =
> good" score is gameable.

So the process axis emits its own verdict, but the useful aggregate is **cost-per-correct-outcome** — a point on
the accuracy–cost Pareto frontier [12][13], computed at the analytics layer, never fused into one number (§8A).

### 12.1 What the grader sees

regin records everything this axis needs: ordered `tool_use` spans (name, inputs, outputs, exit codes,
timestamps) and the token/cost breakdown with cache-read / cache-write / output separated and per-tool
attribution. That cost data is the part no off-the-shelf grader has.

### 12.2 Criterion P1 — TOOL-USE APPROPRIATENESS

> Each tool call was the right instrument, and its output was actually used.

- **Unit:** each `tool_use` span. **Verdict:** `APPROPRIATE` · `SUBOPTIMAL` · `WASTED`.
- **Bars:** `SUBOPTIMAL` — a cheaper/more-direct tool would have done the same (`Bash cat/grep` where
  `Read`/`Grep` exists, a full-file Read where a targeted Grep would do); `WASTED` — the span's output never
  influenced a later span or final claim (checkable via data-flow from this span's output downstream).
- **Anti-gaming:** "used" means the output fed a later decision, not merely that the span exists.

### 12.3 Criterion P2 — REDUNDANCY / THRASH / BACKTRACK

> No repeated work, no spinning without changing approach.

- **Unit:** episodes; **reports counts.**
- **Bars:** redundant read (same `Read` on an unchanged target >once, no mutating span between); thrash (≥K
  consecutive `Bash` spans, same non-zero exit, no intervening edit); re-derivation (re-establishing a
  fact already grounded earlier). Visible only because regin has the timestamped, deduplicable span sequence.

### 12.4 Criterion P3 — TOOL-CALL RELIABILITY

> Errors were the exception and were recovered, not ignored.

- **Unit:** error spans. **Reports:** `E errored / R recovered / I ignored`.
- **Bars:** `recovered` if a later span addresses the cause and succeeds; `ignored` if the agent proceeded
  (especially if it then made a claim depending on the failed step — which the **correctness** axis independently
  catches as `UNGROUNDED`). High errored-call churn is a process smell even when ultimately recovered.

### 12.5 Criterion P4 — COST-PROPORTIONALITY (regin-distinctive)

> Spend was proportionate to task difficulty and value delivered.

- **Unit:** the session's cost profile. **Verdict:** `PROPORTIONATE` · `ELEVATED` · `RUNAWAY`.
- **Reference (absolute cost is meaningless):** (1) per-task-class percentile vs similar traced sessions; (2)
  cost-per-covered-item (ties spend to correctness value — a Pareto point); (3) an explicit budget if set.
- **Context-bloat sub-check:** cache-read tokens are context replay and dominate cost; a session whose
  cache-read share balloons without compaction is paying for an unmanaged context window — flag it. Visible only
  because regin separates cache-read from output tokens.
- **Caveat [13]:** accuracy gains carry disproportionate cost, so high spend on a correct, high-coverage session
  is `PROPORTIONATE`, not wasteful — judge cost against the outcome, never alone.

### 12.6 Scoring & the Pareto framing

```
Process scoreboard:
  Tool-use:  A appropriate / S suboptimal / W wasted   (of T spans)
  Redundancy: <r> redundant reads, <t> thrash episodes
  Reliability: E errored / R recovered / I ignored
  Cost: <percentile or ratio vs class>; cost/covered-item = <x>
  Verdict: <efficient | acceptable | wasteful>
```

- **Gates:** any `ignored` error feeding a downstream claim ⇒ at most `acceptable`; `RUNAWAY` cost ⇒ `wasteful`.
- **Aggregate (analytics layer, not the grader):** plot `(correctness pass?, cost)` on a Pareto frontier per
  task-class. Off-frontier sessions are the interesting ones — *cheaply wrong* (failed, under-verified) and
  *expensively right* (passed, top-decile cost).

### 12.7 The two axes check each other

Each axis catches the other's gaming, which is why running both beats either alone:

| Agent shortcut | Caught by |
|---|---|
| Under-verify to save tokens (skip the test run, assert from memory) | **Correctness** — claims come back `UNGROUNDED` / `STALE` |
| Over-verify / thrash / re-read to look thorough | **Process** — `redundant` / `thrash` episodes, `WASTED` spans |
| Cite a convenient proxy instead of the source | **Correctness** — Source-Quality `PROXY` |
| Let context balloon | **Process** — cost-proportionality context-bloat sub-check |

There's no cheap way to satisfy both axes except by doing grounded, efficient work.

### 12.8 Rubric-as-data schema

```yaml
rubric:
  axis: process_efficiency
  conditioned_on: correctness_verdict
  judge: { model: <strong-model>, isolation: fresh_context, sees: [span_timeline, cost_breakdown, rubric] }
  criteria:
    - id: tool_use_appropriateness
      unit: span
      verdicts: [APPROPRIATE, SUBOPTIMAL, WASTED]
      checks: { suboptimal: cheaper_tool_existed, wasted: output_unused_downstream }
    - id: redundancy
      unit: episode
      reports: [redundant_reads, thrash_episodes, re_derivations]
      checks: { redundant_read: same_target_unchanged, thrash: k_consecutive_same_failure_no_edit }
    - id: reliability
      unit: error_span
      reports: [errored, recovered, ignored]
      gate: { on: ignored_error_feeding_claim, result: cap_acceptable }
    - id: cost_proportionality
      unit: session
      verdicts: [PROPORTIONATE, ELEVATED, RUNAWAY]
      reference: [per_task_class_percentile, cost_per_covered_item, explicit_budget]
      subcheck: cache_read_share_bloat
      gate: { on: RUNAWAY, result: wasteful }
  aggregate: pareto(correctness_pass, cost) per task_class
  ignore: [unavoidable_io_latency, one_off_recovered_errors, exploratory_reads_that_informed_a_decision]
```

### 12.9 Worked example

Same login-500 session, process axis:

```
Tool-use: 9 appropriate / 2 suboptimal / 1 wasted (of 12 spans)
Redundancy: 2 redundant reads, 0 thrash
Reliability: 1 errored / 1 recovered / 0 ignored
Cost: 78th pctile for "bugfix"; cost/covered-item elevated (cache-read share 71%)
Verdict: acceptable

- [span#14,#22] SUBOPTIMAL: read auth.ts twice unchanged — second read was redundant.
- [span#28] WASTED: grepped for "session" but the result fed no later step or claim.
- cost: cache-read share 71% suggests context wasn't compacted mid-session; ~proportionate given the fix landed.
```

Paired with the correctness scoreboard: *correct-but-not-yet-verified* (the `STALE` test claim) **and**
*acceptable-but-slightly-bloated* process.

### 12.10 What to ignore

Unavoidable I/O latency and one-off recovered errors; exploratory reads that *did* inform a decision
(exploration isn't waste — only **unused** output is); absolute token counts divorced from task class.

> **Note on aggregation.** The cost-per-correct-outcome / Pareto framing in §12.6 is the proposed answer to the
> survey's open question on cross-axis aggregation (§9) — it is an inference, not something the cited sources
> settle.

## 13. Claim extraction (the bridge from trace to ledger)

The step that turns a raw regin trace into the typed **claim ledger** the correctness triad (§11) iterates over.
The riskiest step in the system. Surface names below should be verified against regin's actual trace schema at
build time.

### 13.0 Why this step carries the risk — the recall asymmetry

The two error modes are not symmetric:

- A **falsely-extracted non-claim** is *cheap* — the grounding step finds trivial support or marks it
  out-of-scope. Wasted tokens, no harm.
- A **missed claim** is *expensive* — never checked, so the session **silently passes**. This is the exact
  failure the grader exists to prevent.

> **Design rule: tune extraction for recall; let the grounding step filter precision.** Over-extract, then let
> "does a span support this?" discriminate. Extraction must never *omit*.

### 13.1 Scope — what counts as "the artifact"

| Tier | Surface | Treatment |
|---|---|---|
| **Primary** | Final deliverable: last assistant message(s) / final summary, **plus agent-authored prose in the diff** (new code comments, commit/PR message) | Extract exhaustively |
| **Secondary** (opt-in "transcript mode") | Intermediate assistant turns | Extract **only load-bearing claims that fed a later action** |
| **Excluded** | Thinking blocks; tool inputs/outputs; hedges/plans/narration | Tool I/O is **evidence, not claims**; thinking is rationale (§11.10) |

Primary tier mirrors the cookbook (grade the artifact, fresh context). Secondary tier reflects that Anthropic
*also* grades transcripts [11]; keep it opt-in because it inflates the ledger.

### 13.2 The extraction pipeline (10 phases)

```
A Surface select → B Segment → C Checkability filter → D Atomic decompose
→ E Referent resolution → F Type → G Load-bearing flag
→ H Verbatim-provenance guard → I Supersession/dedup → J Completeness critic
```

- **A. Surface select** — pick tiers (§13.1); record each segment's provenance `{surface, turn_id, offset | diff_hunk}`.
- **B. Segment** — split into candidate clauses.
- **C. Checkability filter** — keep only assertions about a verifiable state of the world; drop hedges
  (`might/probably/let me/I'll`), questions, plans, opinion. **Bias toward keeping** (recall).
- **D. Atomic decompose** — break compound assertions into atomic claims, FActScore-style [a], each
  independently checkable, each linked to its parent sentence.
- **E. Referent resolution** — rewrite each claim into a **self-contained** statement, resolving
  `it / this function / the file` to concrete `{file, symbol, command, url}`. The grounder checks
  `normalized_text`, never the raw clause.
- **F. Type** — assign `state | result | external | diagnostic` (§13.3) → selects the grounding span class.
- **G. Load-bearing flag** — does the deliverable's correctness depend on this claim? (§13.4)
- **H. Verbatim-provenance guard** — each claim must cite the **exact substring** it was derived from; no quote,
  no claim. This is the **anti-hallucination guard** — the extractor cannot invent a claim the agent never made.
- **I. Supersession/dedup** — collapse claims restated across turns to **final-state** form; drop explicitly
  retracted claims. Only the end-state is graded (the grader has no memory of prior versions).
- **J. Completeness critic** — a second pass: *"list every verifiable assertion in the artifact NOT already in
  the ledger."* Found items are added. The **primary defense against silent omission** (§13.0).

### 13.3 Typing rules (routing table)

| Type | Linguistic signal | Grounds against (§11.2) |
|---|---|---|
| `state` | "is / does / contains / handles / imports / calls" about code | `Read`/`Grep` span with the lines |
| `result` | "passes / fails / returns / succeeds / errors / builds / is fixed" + runnable referent | `Bash` span: command + exit + stdout |
| `external` | names a library/API/version/doc | `WebFetch` of an authoritative source |
| `diagnostic` | "because / caused by / root cause / due to" | a cause span **and** an effect/repro span |

Ambiguous ⇒ default to the **stricter** type (the one demanding more evidence).

### 13.4 Load-bearing determination

The gate (a `CONTRADICTED` claim ⇒ correctness FAIL, §11.5) fires on claims the answer *depends on*, not asides.

- **Load-bearing:** claims in the final summary; any `result` claim about the deliverable; the diagnostic the
  fix is premised on.
- **Incidental:** background observations. Still reported if `CONTRADICTED`, but cap at `needs_revision`, not `fail`.

### 13.5 The synthetic top-level claim (terse sessions)

Sessions that deliver code with little prose leave the gate nothing to bite. Always inject one synthetic claim:

> `c0` (type `aggregate`, load-bearing): *"the session accomplished `<task>`"* — grounded by the **coverage
> checklist**, not a single span.

### 13.6 Ledger output schema

```yaml
claim:
  id: c4
  raw_text: "the full suite passes"          # verbatim — the H-guard anchor
  normalized_text: "the project's full test suite passes after the edits in this session"
  type: result
  referents: { command: "<test runner>", file: null, symbol: null, url: null }
  provenance: { surface: final_summary, turn_id: 42, offset: 118 }
  load_bearing: true
  parent_sentence: "Added await, added a regression test in auth.test.ts, the full suite passes."
  extraction_confidence: 0.93
```

The grounder consumes `{normalized_text, type, referents, provenance}`; feedback bullets cite `id` + `raw_text`;
`provenance.turn_id` gives the timestamp the **`STALE` rule** (§11.2) compares against span times.

### 13.7 Failure modes & guards

| Failure mode | Guard |
|---|---|
| **Silent omission** of a false claim (the expensive one) | Recall-tuned filter + **completeness critic** (Phase J) |
| Extractor **hallucinates** a claim not in the text | **Verbatim-provenance guard** (Phase H) |
| **Mis-typing** routes to the wrong span class | Default to stricter type; type recorded for audit |
| **Dangling referent** un-resolvable | Phase E flags `referent_unresolved` → grounder marks `UNGROUNDED` |
| Grading a **superseded/retracted** claim | Phase I keeps final-state only |
| **Over-decomposition** into context-stripped fragments | Keep `parent_sentence` for grounder context |
| Incidental wrong claim **hard-fails** a good session | Load-bearing flag (§13.4) scopes the gate |

### 13.8 Worked example

Final summary: *"Root cause was an unawaited `verifyToken()` in `login()`. Added `await`, added a regression
test in `auth.test.ts`, the full suite passes."* → ledger:

```
c0 aggregate   [LB] "session fixed the login 500 + added a regression test"   → grounded by coverage checklist
c1 diagnostic  [LB] "the login 500 was caused by an unawaited verifyToken() in login()"
               referents{file: src/auth/login.ts, symbol: login→verifyToken}   ← "Root cause was an unawaited verifyToken() in login()"
c2 state       [LB] "an `await` was added to the verifyToken() call in login()"  ← "Added await"
c3 state       [LB] "a regression test was added in auth.test.ts"                ← "added a regression test in auth.test.ts"
c4 result      [LB] "the full test suite passes after this session's edits"
               referents{command: <test runner>}                              ← "the full suite passes"
```

D split one sentence into c2/c3/c4; E attached files/commands; the `←` quotes are the H-guard anchors. The
grounder returns c4 `UNGROUNDED`/`STALE` (§11.8) — *because c4 is in the ledger to be checked.* Had extraction
missed c4, the session would have silently passed.

### 13.9 Open risks

- **Extraction is itself an LLM step** subject to the same biases — run low-temperature, structured output, with
  the completeness critic treated as non-optional. It is the single point where a silent pass can originate.
- **Diff-implied claims** the agent never wrote in prose — this design treats the diff as *evidence* and uses the
  synthetic `c0` + coverage checklist as its proxy; per-hunk intent claims are a deferred escalation.
- **Long traces** — the final-deliverable artifact stays small, but transcript mode (secondary tier) does not;
  cap or sample it.

## Sources

- [1] Anthropic, *Building Effective Agents* — https://www.anthropic.com/research/building-effective-agents
- [2] G-Eval (Liu et al., EMNLP 2023) — https://arxiv.org/abs/2303.16634
- [3] MT-Bench / Judging LLM-as-a-Judge (Zheng et al.) — https://arxiv.org/abs/2306.05685
- [4] Rubric position bias (Xu et al., 2026) — https://arxiv.org/html/2602.02219
- [5] LLM-as-judge survey + PairS (Liu et al.) — https://arxiv.org/abs/2411.15594, https://arxiv.org/abs/2403.16950
- [6] Self-preference / concreteness bias — https://arxiv.org/abs/2411.15594 (w/ Panickssery et al., NeurIPS 2024)
- [7] Judge bias / factual-error detection / prompt strictness (Gao et al.) — https://arxiv.org/html/2510.12462v3
- [8] LLM-judge human agreement (MT-Bench Table 5) — https://arxiv.org/abs/2306.05685
- [9] Agent-as-a-Judge — https://arxiv.org/abs/2410.10934
- [10] Agent-as-a-Judge cost/time — https://arxiv.org/abs/2410.10934
- [11] Anthropic, *Demystifying Evals for AI Agents* — https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- [12] Cost-controlled agent evaluation (Kapoor et al.) — https://arxiv.org/abs/2407.01502
- [13] HAL agent leaderboard — https://arxiv.org/abs/2510.11977
- [a] FActScore (Min et al.) — https://arxiv.org/abs/2305.14251
- [b] ALCE — citation precision/recall (Gao et al.) — https://arxiv.org/abs/2305.14627
- [anchor] Anthropic Cookbook, *Verify with Outcome Grader* — https://platform.claude.com/cookbook/managed-agents-cma-verify-with-outcome-grader
