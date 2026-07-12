---
name: goal-verified
description: Run a goal with an independent verifier — pin the bar before building, build then STOP, verify against the bar with a fresh reviewer and the machine gates, then commit. Use for /goal, build, implement, refactor, redesign tasks where you keep finding bugs the agent didn't point out.
---

# goal-verified

A loop-engineering wrapper for `/goal`. It exists to fix one failure mode:
an agent that **builds and verifies its own work always says "looks good"**,
because it cannot see its own blind spots — so you inherit the bugs it
didn't point out. The fix is to put an independent "no"-sayer between the
build and the commit, and to pin a falsifiable bar *before* building so the
verifier has something concrete to check.

regin does not own the agent loop, so this is a procedure you (the agent)
must follow, not something enforced from outside. Follow it in order. Do
not skip the STOP.

## Why this works (read once)

- `/goal` alone stops when the model *runs out of ideas* (`prompt_input_exit`),
  not when a goal is *verified met*. That is why bugs slip through.
- "Make the UI good" is unfalsifiable — there is nothing to check, so "done"
  collapses to a vibe. The roadmap converts it into checkable items anchored
  to standards the repo **already holds** (the same skills/engines the hooks
  enforce), so you are never inventing a bar.
- Division of labour: the **program gathers** candidates (deterministic, high
  recall — never misses a relevant standard) and **you refine** them
  (precision — only judgment grounded in the real code can tell *relevant*
  from merely *related*). Skipping the refine (step 1.5) hands the build a
  noisy bar; that is the difference between a roadmap that helps and one that
  distracts.

## Two ways to run this

- **Inline mode** (default, simplest): you run every step yourself in this one
  context, using a fresh subagent only for the verify in step 4.
- **Agent-arm mode** (stronger isolation): you act as the *orchestrator* and
  delegate the judgment-heavy steps to dedicated, fresh-context subagents.
  Each runs in its own context window, so the verifier literally cannot share
  the builder's blind spots. Prefer this for non-trivial goals.

The named agents (load at session start; if a dispatch errors with "agent type
not found", you are mid-session before they registered — fall back to inline,
or dispatch `general-purpose` with the agent's role pasted as the prompt):

| Step | Agent | Role |
|------|-------|------|
| 1.5 Refine | `goal-refiner` | prune the raw roadmap against the real code (read-only) |
| 3 Build | `goal-builder` | implement against the approved roadmap, run gates, STOP (no commit, no self-grade) |
| 4 Verify | `goal-verifier` | adversarial, read-only; PASS/FAIL with proof. Run **1–3 in parallel** and treat a majority (or any) DO-NOT-SHIP as a wall |

**Running from the `regin-agents` plugin instead of this repo:** plugin-shipped
agents register namespaced (`regin-agents:goal-builder`, not bare
`goal-builder`), and bare names only resolve when a same-named agent also
exists outside the plugin. In a plugin-only install, dispatch the qualified
`regin-agents:goal-builder` / `regin-agents:goal-refiner` /
`regin-agents:goal-verifier` form so the agent-arm doesn't silently fall back
to inline.

**Preflight (step 1) and feedback (step 6) stay programs in both modes** —
they are the deterministic gather and record; only the judgment steps become
agents. The orchestrator (you) owns the human checkpoint (step 2 approval),
the fix loop, and the commit; never let a worker agent commit.

### Two tiers of recall (agent-arm)

The step-1 recall (`recall-for-task`) runs **once** and feeds *your* bar
(the roadmap). But a freshly-spawned `goal-builder` / `goal-verifier` starts
with **none of that** — the harness can't inject memory into a subagent, so
whatever a worker should know must be **baked into its prompt by you, the
spawner**. So in agent-arm mode, before dispatching a worker, run a second,
**stage-scoped** recall and prepend its block to the worker's prompt:

```bash
SID=$(regin session-id)
regin memory recall-for-task \
  "<this stage's task>" [--subsystem <topic-node-id>] --session "$SID"
```

This is **structure-first** (it pulls the subsystem's filed memories by the
topic tree, ranked by importance — *not* by similarity to the stage wording),
so a build/verify-phrased task still surfaces the right subsystem experience.
Pass `--subsystem` with the node id you identified while reading the code in
step 1.5; omit it to let the task text route. Anti-skip: it leaves a
`memory.recall.task` span, and **`mcp__memory__gate(name="task-recall-ran",
session_id="<your $CLAUDE_CODE_SESSION_ID>")` PASSes iff a worker was armed this
run** (or the `regin gate task-recall-ran --session "$SID"` CLI where regin is
installed) — treat a `GATE FAIL` before commit as a wall (the same span-gate
discipline `goal-verified-treenav` uses for its recall arm).

## Procedure

### 1. Preflight — pin the bar (hard gates + structure-first lessons)
Two parts. First the **deterministic gate floor** — the universal hard gates
every run must pass (existing tests stay green; an independent fresh-context
reviewer checked the diff). Preflight emits these (plus an opt-in lessons
recall); it no longer routes per-area skills/references — that area table was
retired because it only restated the file-keyed convention table and never
generalized to other repos (no embeddings, no guessing):

```bash
SID=$(regin session-id)   # prints THIS session's id
regin goal preflight "<the full goal string>" --session-id "$SID"
```

`regin session-id` is a real CLI command: a thin wrapper that prints the
`CLAUDE_CODE_SESSION_ID` env var Claude Code exports into every Bash call (see
`lib/session_probe.py`). Run it via `regin session-id`.
If it ever comes back empty, omit the flag (you lose only the
offered-recording, not the roadmap).

For the **convention skills**, read the ones the file-keyed table in
`CLAUDE.local.md` maps to the files you'll touch *before* writing code
(convention guides backed by rule engines — reading first avoids the
round-trip). For **reference components**, open the 1–2 real target files in the
refine step (1.5) and mirror the closest existing module — don't invent new
patterns.

> **Lessons no longer come from preflight.** Its old flat-FTS lessons leg is
> demoted (`--no-lessons` is the default — it measured ~22% engagement). Recall
> lessons **structure-first** instead, off the topic tree:
>
> ```bash
> regin memory recall-for-task "<the full goal string>" \
>   [--subsystem <topic-node-id>] --session "$SID"
> ```
>
> It prints a `<recalled_experience>` block of the goal's subsystem memories
> (ranked by importance, not text similarity) and **auto-records them as offered**
> (the engagement denominator), so you don't pass `--session-id` lessons by hand.
> Pass `--subsystem` if you can already name the area; omit to route from the goal
> text. **Note the memory ids it surfaced** — you report which you used in step 6.
> (To A/B the retired flat leg on this goal, also run `goal preflight … --with-lessons`
> and diff the two id sets.)

### 1.5. Refine — prune the roadmap against the real code (DO NOT SKIP)
The roadmap is **high-recall on purpose**: the scaffold routes off goal *text*,
so it over-includes. A single word can fire a whole extra area (e.g. "session"
pulling in trace/Python skills, `lib/**` refs, and a pytest gate for a
pure-Vue change), the structure-first recall can pull a whole subsystem's
lessons when only one applies, and the file you actually need to edit may not
even rank into the references. A roadmap taken raw is a noisy bar — and a build
against a noisy bar over-scopes and gets distracted. The program can gather
candidates; only you, reading the code, can judge which are *relevant*.

So before deriving the checklist, **open the 1–2 real target files** the goal
names (find them if preflight missed them) and refine the roadmap in place:

- **Drop wrong-area noise.** If the change is single-area, cut the skills,
  references, and gates dragged in by an over-fired area. (Pure-frontend? Drop
  the Python skills, the `lib/**` refs, and the pytest gate.)
- **Keep only lessons that apply.** For each recalled memory id, keep it *only*
  if it bears on this change as seen in the actual file; drop subtree-neighbour
  matches that merely share the subsystem. Note which you dropped and why (one
  line) — that is itself signal.
- **Add what recall missed.** Put the real edit target(s) at the top of the
  references even if the glob didn't surface them.
- **Promote concrete violations you can already see** in the file into
  candidate checklist items (e.g. "file renders `✓`/`↓` as raw glyphs →
  replace with `<Icon>` per the ui/Icon lesson").

Ground every keep/drop in something you read in the code, not in the goal
string. The output of this step is the *pinned* roadmap that goes to approval.

**Agent-arm:** dispatch `goal-refiner` with the goal + the raw roadmap; it
returns the pruned roadmap + a Dropped list + Visible violations. Carry those
violations into step 2.

### 2. Roadmap — derive the acceptance checklist, get it approved
Working from the **refined** roadmap (step 1.5), fill the **Acceptance
checklist** — the one judgment step. Turn the goal + kept standards into
**3–8 falsifiable items**: concrete behaviors with edge cases (states at
0 / 1 / N items, filter counts vs the API, empty/loading/error states), each
verifiable by someone who did not write the code. **Fold in the lessons you
kept** and the violations you already spotted in step 1.5 — they are pre-paid
bug reports. Then **show the user the full
roadmap and the checklist and get a yes** before building. This is their
15-second checkpoint; it replaces them hand-writing the bar. Record which
lesson-ids you folded in (the *included* set) for step 6.

### 3. Build — then STOP
Implement against the roadmap. Reuse the reference components and the design
tokens; do not introduce new colors, spacing, or one-off components.
When you believe it is done: **STOP. Do not commit. Do not self-congratulate.**

**Agent-arm:** first arm the worker — `regin memory recall-for-task "<the build
task>" --subsystem <node> --session "$SID"` (see *Two tiers of recall*) and
prepend its `<recalled_experience>` block to the dispatch. Then dispatch
`goal-builder` with that block + the goal + the approved roadmap + the
acceptance checklist; it returns the diff, the gate output it ran, and a
per-item acceptance status. It will not commit or self-grade — that is by
design.

### 4. Verify — independent, adversarial
Hand the work to a checker that did **not** build it:

- **Fresh-context reviewer:** `/code-review high`, or spawn an agent with:
  *"You did NOT write this. The branch claims <goal> is done. Assume it is
  broken. Check each acceptance item PASS/FAIL with proof. Find empty/edge
  states, filter counts that don't match, console errors, untested paths."*
- **Machine gates** from the roadmap — run them for real, paste the output:
  - frontend: `cd frontend && npx vite build` and
    `cd frontend && ./node_modules/.bin/playwright test`; zero console errors.
  - **UI goals (`.vue`) — `ui-verified` gate:** re-run it against the build
    session — `mcp__memory__gate(name="ui-verified", session_id="<build sid>")`
    (or `regin gate ui-verified --session "$SID"`). `GATE FAIL` / `0` browser
    spans means the UI was never rendered, only diffed — a **DO-NOT-SHIP wall**,
    however good the code looks. The gate proves *a* render happened, not which
    viewport: the verifier must confirm the render covers **both desktop and
    ~390px mobile** (where most layout breakages land) and that
    `scrollWidth<=clientWidth` holds — a desktop-only render passes the span
    gate but fails this item. Playwright driven as a Bash node script does NOT
    count; use the traced MCP browser tools so the render leaves a span.
  - python: `.venv/bin/python -m pytest <relevant>`; radon grade ≥ C; grit clean.
- A gate that fails is a **wall**, not a note. Do not proceed past a red gate.

**Agent-arm:** first arm the verifier with **how this subsystem has failed
before** — the highest-value recall for a checker: `regin memory recall-for-task
"verify <goal>" --subsystem <node> --session "$SID"` (see *Two tiers of
recall*), and prepend its block to the dispatch. Then dispatch `goal-verifier`
with that block + the goal + acceptance checklist + the diff. For non-trivial
goals run **1–3 verifiers in parallel** (send them in one message) and treat
*any* DO-NOT-SHIP as a wall — independent contexts catch different failures. The
verifier is read-only; it reports, it does not fix.

### 5. Fix and re-verify
Feed every FAIL back into the build. Re-run step 4. Only when every
acceptance item passes and every gate is green do you continue.

### 6. Commit, then close the loop
Now commit (and only now). Reference the goal; note which gates passed.

**Agent-arm precondition:** `mcp__memory__gate(name="task-recall-ran",
session_id="<the run's session id>")` must PASS — proof you armed the workers
with stage-scoped recall (step 3/4) (the `regin gate task-recall-ran --session
"$SID"` CLI works too where regin is installed). A `GATE FAIL` means you
dispatched a builder/verifier blind; that is a wall, not a note.

Then feed the outcome back into memory so the *next* run starts smarter:

```bash
SID=$(regin session-id)   # same probe as step 1; links these writes to the run
regin goal feedback "<goal>" \
  --included <memory-id-you-used> \
  --fail "An acceptance item that FAILED, phrased as a transferable RULE" \
  --tag <area, e.g. frontend> \
  --topic <topic-node-id> --trace-id "$SID"
```

Always pass `--trace-id "$SID"`: without it the new failure-lessons land with
`source_trace_id = NULL` and can't be traced back to the run that produced them.

- `--included` reinforces the memories that earned their place in the approved
  roadmap — the ids `recall-for-task` surfaced in step 1 that you actually
  folded in. Pass each once.
- `--offered` is **no longer needed by hand**: `recall-for-task` already
  auto-recorded everything it surfaced as offered (step 1), so the engagement
  denominator is captured. Unused offered ids decay naturally.
- `--fail` writes each verification failure as a **new lesson** (phrase it
  as a rule, not "what happened in this session"), tagged so the next run
  recalls it. This is the mechanism that turns today's bug into next week's
  recalled warning.
- `--topic` (repeatable) files each `--fail` lesson straight under an
  authoritative topic node — pass the node id of the subsystem the lesson
  belongs to (here `recall-for-task` already routed you to one in step 1, so
  reuse it; a slashed `parent/child` short-path also works, only the leaf is
  the id). An unmatched short-path is reported, not fatal, and the lesson is
  still written. This files the new lesson by subsystem now instead of
  waiting for the async classifier. **When no node honestly fits** (the goal
  sat outside any subsystem), pass **`--topic none`** (or `-`) — resolution is
  exact-only (node id or slashed leaf; **no** fuzzy keyword fallback), so a
  near-miss word like `--topic skills` is *not* silently misrouted to a wrong
  node: it is reported unresolved and left unfiled. `none` makes the "no
  related topic" choice explicit and warning-free instead of forcing a guess.
- **Refresh a lesson your fix invalidated (conditional — usually skip).** The
  feedback above only *adds* to memory; it never corrects it. If a memory
  `recall-for-task` surfaced in step 1 described a behavior this change just
  made obsolete — a bug you removed, a mechanism you deleted — don't leave it
  to mislead future recall, and don't hard-`forget` it (you'd lose the *why*).
  **Supersede** it: keep the still-true guidance, retire only the dead
  mechanics, cite the commit.
  ```bash
  regin memory supersede <stale-id> \
    --title "<refreshed title>" \
    --body "<kept guidance + 'X removed in <commit>'>"
  ```
  Trigger only on surfaced ids the *verified* change made false — most runs
  supersede nothing. This is what stops a self-growing loop from poisoning its
  own recall with lessons it has since invalidated.

### 7. Refresh any topic wiki your change stranded
Your commit may have moved code out from under a topic's wiki (`drifted`) or
added code to an area that has an approved topic but no wiki yet (`missing`).
Catch it now, while you still know what you touched — don't leave it for a
blind cron pass:

```bash
regin topics wiki-debt --changed-since <base> --emit
```

`<base>` is the commit your goal branched from (`HEAD~1` for a single commit,
or the branch's merge-base). It lists — **scoped to your diff** — the topics
that are `missing` or `drifted`, and the whole command returns in well under a
second.

**Do not draft a wiki inline.** Re-deriving a wiki spawns a tool-using agent
that runs for *minutes* (`create_proposal_run` blocks; a CLI `topics evolve`
auto-spawn returns at once but its background draft dies when the command
exits). Never block — or strand — your turn on it. `--emit` does only the fast,
non-blocking half:
- **`drifted`** → emits an agent-free *stub* refresh proposal (a pure DB write,
  idempotent id `content-drift-<topic>`); the actual re-draft runs later
  server-side. Drop `--emit` if you only want to report.
- **`missing`** → stays report-only (no agent-free way to author a new wiki);
  the human triggers the draft from the topics UI, where it runs async in the
  long-lived server.

Either way the result is a `pending_review` item — **you do not accept it.**
Report what you found (the `drifted`/`missing` list + any stub ids) and let the
human approve, exactly as for the `goal feedback` lessons above. A clean "No
wiki debt" means your change stranded no wiki — say so and move on.

## Gotchas

- **Don't fuse build and verify in one prompt.** "Build it and verify it"
  is the original sin — it asks the typo-maker to proofread. The STOP in
  step 3 is load-bearing.
- **The reviewer must have fresh context.** A reviewer that watched the build
  inherits the same blind spots. Use `/code-review` or a new agent, not
  "now review your own work".
- **Globbed references reflect the current branch.** If preflight surfaces
  the wrong siblings, the branch may predate the component you meant — name
  the reference by hand.
- **Preflight needs the repo's `.venv`.** Run it from the repo root with
  `.venv/bin/python`, not the system interpreter.

## How this compounds

The loop is closed: structure-first `recall-for-task` **recalls** past lessons
into the roadmap off the subsystem tree (front), and `goal feedback` **writes**
verification failures back as new lessons + reinforces the ones that helped
(back). Each run a goal-type has been through makes the next run's roadmap
sharper — your one-off corrections accumulate into the standard the goal never
had. The lessons ride the same `lib/memory` store as `send_to_user(type=lesson)`,
so they also surface as `<recalled_experience>` in ordinary sessions, not only
under this skill.
