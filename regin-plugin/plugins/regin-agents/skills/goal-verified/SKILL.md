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
or paste the agent's role into a generic stand-in. For the **read-only** roles
pick a read-only generic agent (an `Explore`-style search agent, if your harness
has one), since a `general-purpose` agent holds every tool including `Edit`,
`Write` and `Agent`, which turns a read-only guarantee into prose and lets a
stand-in verifier recursively spawn more agents; use `general-purpose` only for
the builder):

| Step | Agent | Role |
|------|-------|------|
| 1.5 Refine | `goal-refiner` | prune the raw roadmap against the real code (read-only) |
| 3 Build | `goal-builder` | implement against the approved roadmap, run gates, STOP (no commit, no self-grade) |
| 4 Verify | `goal-verifier` | adversarial, read-only; PASS/FAIL with proof. Dispatched **serially, #1 owns the gates** (step 4); any DO-NOT-SHIP is a wall |

**Running from the `regin-agents` plugin instead of this repo:** plugin-shipped
agents register namespaced (`regin-agents:goal-builder`, not bare
`goal-builder`), and bare names only resolve when a same-named agent also
exists outside the plugin. In a plugin-only install, dispatch the qualified
`regin-agents:goal-builder` / `regin-agents:goal-refiner` /
`regin-agents:goal-verifier` form so the agent-arm doesn't silently fall back
to inline.

## Running on a harness other than Claude Code

Nothing in the loop is Claude-specific *in principle*, but three steps used to
reach for tools only Claude Code has. Each has a shell-command form, so the loop
runs wherever regin's CLI does:

| Step | Claude-only form | Portable form |
|---|---|---|
| session id | `$CLAUDE_CODE_SESSION_ID` | `regin session-id` reads `$REGIN_SESSION_ID` first — **export it from your harness or its wrapper; that is the reliable route.** Failing that, `regin session-id --from-trace` returns the session regin's hooks recorded for this directory, but only when exactly one has been active in the last 30 minutes — two concurrent agents, or none, print nothing. |
| recall / tree walk | `mcp__memory__recall`, `index_*`, `memory_read` | `regin memory recall`, `index-root` / `index-expand` / `index-fetch`, `read` — one shared renderer, so the same text |
| anti-skip gate | `mcp__memory__gate(…)` | `regin gate <name> --session "$SID"` — exit 0 PASS, 1 FAIL, 2 INCONCLUSIVE |
| fresh-context verify | `goal-verifier` subagent, `/code-review high` | a **second process** of your CLI, started clean, handed the goal + acceptance checklist + `git diff` and told it did not write the code. The fresh process is the isolation that matters; the subagent tool is only the convenient way to get one. |

Pass `--session "$SID"` to the `memory` commands: that is what leaves the span
the gate counts, so a run with no MCP at all that did the recall arm honestly
still PASSes instead of reading as a skip. Two caveats the gate itself will tell
you about, rather than silently failing you: a walk whose span the ingest
refused prints a warning on stderr (start `regin serve`, re-run), and
`regin gate` with an empty `--session` returns **INCONCLUSIVE**, not FAIL —
without an id there is nothing to count, so that is not evidence you skipped.

**Agent-arm mode stays Claude-only.** Steps 1.5 / 3 / 4 dispatch named
subagents, which needs a harness-level subagent tool. On any other harness run
**inline mode** and get the fresh context for step 4 from a second CLI process;
everything else in the loop is identical.

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
`memory.recall.task` span, and **`regin gate task-recall-ran --session "$SID"`
PASSes iff a worker was armed this run** (the `mcp__memory__gate(name=
"task-recall-ran", session_id="$SID")` tool is the same check where the memory
MCP is loaded) — treat a `GATE FAIL` before commit as a wall (the same span-gate
discipline `goal-verified-treenav` uses for its recall arm).

## Triage gate — size the goal before you pay for the loop

Run this first, every time. The loop below is priced for a multi-file,
behavior-changing goal; run it whole on a typo and the ceremony costs more
than the bug. Measured over 229 sessions using this loop (or its `-treenav`
sibling): the median ran **80 minutes of engaged time vs 48 for comparable
sessions without it**, and the overhead was volume, not slowness — the same
test suite re-run by the main agent, then the builder, then each verifier.

| Goal | What to run |
|---|---|
| **Purely visual, one component** (CSS/layout/overlap/spacing/copy) | Skip this loop. Read the component and its reveal-path, edit, and verify with measured DOM geometry in a browser — the recall ceremony buys nothing for a defect that is plain in a screenshot. |
| **Small**: one file, ≤20 lines, no new behavior | Preflight + one `recall-for-task`, **skip 1.5**, build, **one** verifier, gates scoped to the touched paths. |
| **Normal**: multi-file, or changes behavior | The full loop below. |
| **Large**: ≥2 subsystems, or a migration | The full loop, 2–3 verifiers, whole-suite gates. |

The steps below are written for **Normal**. Every step says what Small drops.

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

`regin session-id` is a real CLI command. It resolves provider-agnostically
(`lib/session_probe.py`): `$REGIN_SESSION_ID` first, then the running CLI's own
variable (`CLAUDE_CODE_SESSION_ID` under Claude Code). If it comes back empty,
either omit the flag (you lose only the offered-recording, not the roadmap) or
add `--from-trace` — see *Running on a harness other than Claude Code* above.

For the **convention skills**, do **not** read them up front. Where your repo's
file-keyed convention table (e.g. in `CLAUDE.md` / `CLAUDE.local.md`) is backed
by edit-time rule engines, they fire on every edit and name the rule you broke —
read the mapped guide only once a violation actually lands on a file you
touched. Reading all of them for a mixed multi-language change is several skill
loads to prevent a round-trip that usually never happens. For **reference
components**, open the 1–2 real target files in the refine step (1.5) and mirror
the closest existing module — don't invent new patterns.

The **hard gate on tests names its flags**: use your test runner's **parallel**
form and **scope it to the paths you touched** (e.g. a parallel-execution plugin
plus an explicit path list, rather than a bare whole-suite invocation). A serial
whole-suite run is routinely an order of magnitude slower than the parallel
scoped one, and a harness typically kills a long shell call (regin's ceiling is
600s): in the measured corpus 13 full-suite runs hit that ceiling and returned
*nothing* after paying 10 minutes each. Run the **whole** suite only when the
diff touches a cross-cutting foundation (the ORM/schema layer, the settings
singleton, the hook plumbing); otherwise it belongs to CI, not to this loop.

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

### 1.5. Refine — prune the roadmap against the real code
**Do not skip this for a Normal or Large goal** (Small skips it per the triage
gate — with one target file and ≤4 kept lessons there is no over-recall left
to prune, and a `goal-refiner` dispatch is a full context prefill spent
curating something you already curated). Dispatch the refiner only when recall
surfaced **≥5 lessons** or the diff spans **≥2 areas**; below that, refine
inline while you read the target file.

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

**Agent-arm:** dispatch `goal-refiner` with the goal + the raw roadmap + **the
absolute paths of the target files you already identified**; it returns the
pruned roadmap + a Dropped list + Visible violations. Carry those violations
into step 2. Name the paths: the refiner is budgeted to ≤3 files, and a
dispatch that makes it hunt for the target spends that budget on Glob/Grep
instead of on pruning.

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

**Autonomous mode — self-approve.** If the user is not sitting there (overnight
run, batch job, a goal handed off with "just do it"), do **not** block on a
handshake: post the roadmap and checklist as a summary message and proceed in
the same turn. The checkpoint's purpose is a reviewable artifact, not a wait
state — and a blocking wall is the single largest slice of a long session's
wall-clock, since the human's reply arrives whenever it arrives.

### 3. Build — then STOP
Implement against the roadmap. Reuse the reference components and the design
tokens; do not introduce new colors, spacing, or one-off components.
When you believe it is done: **STOP. Do not commit. Do not self-congratulate.**

**Agent-arm:** first arm the worker — `regin memory recall-for-task "<the build
task>" --subsystem <node> --session "$SID"` (see *Two tiers of recall*) and
prepend its `<recalled_experience>` block to the dispatch. Then dispatch
`goal-builder` with that block + the goal + the approved roadmap + the
acceptance checklist + **the absolute paths of the files to edit and of the
reference components to mirror**; it returns the diff, the verbatim gate output
it ran, and a per-item acceptance status. It will not commit or self-grade —
that is by design. Naming the paths is the point: a fresh context that has to
rediscover them re-Globs, re-Greps and re-Reads what you already read, and
across the measured runs 29% of the files a subagent opened had already been
read by its parent in the same session.

### 4. Verify — independent, adversarial
Hand the work to a checker that did **not** build it:

- **Fresh-context reviewer:** `/code-review high`, an agent, or a second CLI
  process, given:
  *"You did NOT write this. The branch claims <goal> is done. Assume it is
  broken. Check each acceptance item PASS/FAIL with proof. Find empty/edge
  states, filter counts that don't match, console errors, untested paths."*
- **Machine gates** from the roadmap — run them for real, paste the output:
  - frontend: the production build, then the browser suite **scoped to the
    specs covering the changed route** plus the repo's responsive/layout spec;
    zero console errors. A full sweep of every spec is a CI job, not a gate you
    re-run per fix iteration.
  - **UI goals — render it, don't assert it from the diff.** The goal is not
    "a browser was opened", it is "the invariant holds at the widths where it
    breaks". Prove it with a *failing-without-the-fix* test rather than a
    screenshot: add or extend a case in the repo's browser suite and confirm it
    goes red when the fix is stashed — an assertion that passes on the broken
    build is worth nothing. Cover **both desktop and ~390px mobile**; most
    layout breakage lands only on the narrow one. Measure the app's real
    scroll container, not `documentElement` — a wrapper with
    `overflow-x:hidden` above it keeps the document from ever scrolling
    sideways, so a documentElement check passes on a visibly broken page.
  - non-frontend: run the target repo's own test suite — in its **parallel**
    form, scoped to the touched paths — plus its lint/complexity gates (e.g.
    `pytest`, a complexity-grade threshold, a linter); all green.
- A gate that fails is a **wall**, not a note. Do not proceed past a red gate.

**Agent-arm:** first arm the verifier with **how this subsystem has failed
before** — the highest-value recall for a checker: `regin memory recall-for-task
"verify <goal>" --subsystem <node> --session "$SID"` (see *Two tiers of
recall*), and prepend its block to the dispatch. Then dispatch `goal-verifier`
with that block + the goal + acceptance checklist + the diff.

**Split the roles, don't clone them.** `goal-builder` already ran the gates; a
verifier told to "run the machine gates yourself, for real" runs them again,
and three parallel verifiers run them three more times. In the measured corpus
`goal-verifier` alone re-ran the test suite 833 times (7.2h) and the browser
suite 595 times (3.6h) — most of it work the builder had just done. So:

- **Small goal → one verifier**, dispatched with `GATE OWNER: yes`.
- **Normal/Large → dispatch verifier #1 alone first**, with `GATE OWNER: yes`.
  When it returns, dispatch #2 (and #3 only if the diff spans ≥2 subsystems)
  **in one message, in parallel**, each with `GATE OWNER: no` and #1's verbatim
  gate output pasted in. The serialization is deliberate: it costs one
  round-trip and saves two full suite runs. Dispatching all three at once
  cannot work — #2 and #3 would have nothing to reuse, and a verifier that is
  handed no gate output and no owner flag falls back to running everything.

**Every verifier dispatch carries:** the recall block, the goal, the acceptance
checklist, the diff, an explicit `GATE OWNER: yes|no` line, the builder's
verbatim gate output (for non-owners), and **the pre-change gate baseline** you
recorded in step 5 — a verifier given no baseline cannot tell your red test
from the repo's, and will either re-run it under `git stash` or call it a wall.

Treat *any* DO-NOT-SHIP as a wall — independent contexts catch different
failures. `UNPROVEN` is not a wall: it is a named gap, and you decide whether
it is worth a second verifier. The verifier is read-only; it reports, it does
not fix.

### 5. Fix and re-verify — capped at 2 iterations
**Before the first iteration, record the pre-change gate baseline**
(`git stash && <the gate> && git stash pop`). A FAIL that reproduces on the
baseline is a *pre-existing* failure, not your wall — a repo may carry known
red tests, and a verifier instructed to bias toward FAIL when uncertain will
report them as blockers forever.

Feed every genuine FAIL back into the build and re-run step 4 with the
role-split above. **Cap at two fix iterations.** On a third round of FAILs,
stop and report to the human with the surviving list: an item that survives two
adversarial rounds is a scope or spec problem, not a code problem, and further
rounds burn a builder plus verifiers each time without converging.

### 6. Commit, then close the loop
Now commit (and only now). Reference the goal; note which gates passed.

**Agent-arm precondition:** `regin gate task-recall-ran --session "$SID"` must
PASS — proof you armed the workers with stage-scoped recall (step 3/4). A
`GATE FAIL` means you dispatched a builder/verifier blind; that is a wall, not a
note.

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
- **`regin` must be on PATH.** Preflight / gate / feedback shell out to the
  `regin` CLI (the plugin's documented boundary); invoke it from PATH, not a
  checkout-local interpreter.

## How this compounds

The loop is closed: structure-first `recall-for-task` **recalls** past lessons
into the roadmap off the subsystem tree (front), and `goal feedback` **writes**
verification failures back as new lessons + reinforces the ones that helped
(back). Each run a goal-type has been through makes the next run's roadmap
sharper — your one-off corrections accumulate into the standard the goal never
had. The lessons ride the same `lib/memory` store as `send_to_user(type=lesson)`,
so they also surface as `<recalled_experience>` in ordinary sessions, not only
under this skill.
