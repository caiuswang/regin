---
name: goal-verified-treenav
description: The tree-nav arm of goal-verified — same independent-verifier build loop, but the recalled-lessons leg is swapped from preflight's flat embedding/keyword recall to coarse-to-fine topic-tree navigation (memory-tree-nav). The recall leg is gated by a trace-verified span check that makes silently skipping it a wall, not an option. Use to run /goal, build, implement, refactor tasks when you want to pin the bar by *browsing the knowledge tree by subsystem* instead of routing the goal text through the recall ranker — and to A/B the two recall mechanisms head-to-head.
---

# goal-verified-treenav

This is a **sibling of `goal-verified`** that exists for one reason: to swap
the *recall mechanism* and compare. Everything about the loop — pin a
falsifiable bar before building, build then STOP, verify with a fresh
adversarial reviewer + machine gates, fix, commit, feed the outcome back — is
**identical** to `goal-verified`. The only change is **how step 1 recalls past
lessons into the bar**:

| | recalled-lessons leg | how it pulls |
|---|---|---|
| `goal-verified` | flat recall (`goal preflight --with-lessons`) | routes the **goal text** through the recall ranker → a flat, importance-ordered list (recall-first, may surface vocabulary-only matches) |
| `goal-verified-treenav` (this) | `memory-tree-nav` (Glob/Read over `.regin/memory/tree/`, paired with one flat `recall`) | **you walk the topic taxonomy by subsystem** as directories, and read only the leaves the goal touches (precision-first, explainable path) |

The rest of the bar — convention **skills**, **reference components**, **hard
gates** — is *not* routed by a command in this arm: the hard gates are the
universal floor (stated inline in step 1a), the convention skills come from the
file-keyed table in `CLAUDE.local.md`, and the reference components come from
the topic leaf's source refs (step 1b). So the *only* variable that differs
from `goal-verified` is the way lessons enter the roadmap — same loop, same
scaffold, different recall.

regin does not own the agent loop, so this is a procedure you (the agent) must
follow, not something enforced from outside. Follow it in order. Do not skip
the STOP.

## When to pick this over `goal-verified`

- The goal sits squarely in a **subsystem you can name** (trace, eval-grading,
  rule-engines, webui, agent-memory…) — tree nav routes by structure, so a
  nameable area is its strength.
- The goal text is **terse or metaphor-laden**, where preflight's text router
  would key off noise — browsing labels sidesteps the bad query.
- You explicitly want to **compare** the two recall arms on the same goal
  (run both, diff the lesson sets, see which produced the sharper bar).

Prefer plain `goal-verified` when the goal spans many areas at once (a flat
recall sweep beats N tree walks) or when you have no sense of which subsystem
it lands in.

## Two ways to run this

Same as `goal-verified`: **inline** (you run every step in this context, fresh
subagent only for the verify) or **agent-arm** (you orchestrate; the
judgment-heavy steps go to fresh-context subagents). The named agents are the
same registry:

| Step | Agent | Role |
|------|-------|------|
| 1.5 Refine | `goal-refiner` | prune the raw roadmap against the real code (read-only) |
| 3 Build | `goal-builder` | implement against the approved roadmap, run gates, STOP (no commit, no self-grade) |
| 4 Verify | `goal-verifier` | adversarial, read-only; PASS/FAIL with proof. Dispatched **serially, #1 owns the gates** (step 4); any DO-NOT-SHIP is a wall |

Running from the `regin-agents` plugin? The agents register namespaced — see
`REFERENCE.md`. If a dispatch errors with "agent type not found", fall back to
inline, or paste the role into a generic stand-in — for the **read-only** roles
pick a read-only generic agent (an `Explore`-style search agent, if your harness
has one), since a `general-purpose` agent holds every tool including `Edit`,
`Write` and `Agent`, which turns a read-only guarantee into prose and lets a
stand-in verifier recursively spawn more agents; use `general-purpose` only for
the builder. The orchestrator (you) owns the human checkpoint (step 2), the
fix loop, and the commit; never let a worker commit.

## Running on a harness other than Claude Code

Nothing in this arm is Claude-specific *in principle*, but several steps used to
reach for tools only Claude Code has. Each has a shell-command form, so the loop
runs wherever regin's CLI does:

| Step | Claude-only form | Portable form |
|---|---|---|
| session id | `$CLAUDE_CODE_SESSION_ID` | `regin session-id` reads `$REGIN_SESSION_ID` first — **export it from your harness or its wrapper; that is the reliable route.** Failing that, `regin session-id --from-trace` returns the session regin's hooks recorded for this directory, but only when exactly one has been active in the last 30 minutes — two concurrent agents, or none, print nothing. |
| the 1b walk | `mcp__memory__index_*` | `regin memory index-root` / `index-expand` / `index-fetch` (the default here — same renderer, same text, always live), falling back to `Glob`+`Read` over the exported `.regin/memory/tree/` where no CLI is available — note the gate counts the `Read`, never the `Glob` |
| flat recall | `mcp__memory__recall`, `memory_read` | `regin memory recall`, `regin memory read` |
| anti-skip gate | `mcp__memory__gate(…)` | `regin gate recall-ran --session "$SID"` — exit 0 PASS, 1 FAIL, 2 INCONCLUSIVE |
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

## Triage gate — size the goal before you pay for the loop

Run this first, every time. The loop below is priced for a multi-file,
behavior-changing goal; run it whole on a typo and the ceremony costs more
than the bug. Measured over 229 sessions using this loop (or its `goal-verified`
sibling): the median ran **80 minutes of engaged time vs 48 for comparable
sessions without it**, and the overhead was volume, not slowness — the same
test suite re-run by the main agent, then the builder, then each verifier.

| Goal | What to run |
|---|---|
| **Purely visual, one component** (CSS/layout/overlap/spacing/copy) | Skip this loop. Read the component and its reveal-path, edit, and verify with measured DOM geometry in a browser — the recall ceremony buys nothing for a defect that is plain in a screenshot. |
| **Small**: one file, ≤20 lines, no new behavior | 1b's flat `recall` only (no walk), **skip 1.5**, build, **one** verifier, gates scoped to the touched paths. |
| **Normal**: multi-file, or changes behavior | The full loop below. |
| **Large**: ≥2 subsystems, or a migration | The full loop, 2–3 verifiers, whole-suite gates. |

The steps below are written for **Normal**. Every step says what Small drops.

## Procedure

### 1a. The deterministic scaffold — gates inline, skills from the table
This arm does **not** run `goal preflight` (its area router was retired — it
only restated the convention table and never generalized to other repos). The
scaffold is fixed and tiny:

- **Hard gates (the universal floor — the loop may NOT exit until both pass):**
  1. the tests covering the files you touched stay green. **Name the flags:**
     use your test runner's **parallel** form and **scope it to the touched
     paths**. A serial whole-suite run is routinely an order of magnitude
     slower than the parallel scoped one, and a harness typically kills a long
     shell call (regin's ceiling is 600s): in the measured corpus 13 full-suite
     runs hit that ceiling and returned *nothing* after paying 10 minutes each.
     Run the **whole** suite only when the diff touches a cross-cutting
     foundation (the ORM/schema layer, the settings singleton, the hook
     plumbing) — otherwise it belongs to CI, not to this loop.
  2. an independent fresh-context reviewer checked the diff
     (`/code-review high`, an agent, or a second clean CLI process).

  Then add the *area's* machine gates from your repo's file-keyed convention
  table (e.g. in `CLAUDE.md` / `CLAUDE.local.md`) for the files you touch —
  e.g. the test suite + a complexity-grade threshold + a linter for backend
  sources; a production build + bundle/style checks for frontend components.
  Scope the browser suite to the specs covering the changed route plus the
  repo's responsive/layout spec; a full sweep of every spec is a CI job, not a
  gate you re-run per fix iteration.
- **Convention skills:** do **not** read them up front. Where that table is
  backed by edit-time rule engines, they fire on every edit and name the rule
  you broke — read the mapped guide only once a violation actually lands on a
  file you touched. Reading all of them up front for a mixed multi-language
  change is several skill loads to prevent a round-trip that usually never
  happens.
- **Reference components:** come from the tree leaf in step 1b (the topic
  wiki's source refs) — mirror them, don't invent new patterns.

Grab this session's id now — the step-2 gate and step-6 feedback both need it:

```bash
SID=$(regin session-id)   # THIS session's id; add --from-trace on a harness
                          # that exports none (see the table above)
```

### 1b. Tree-nav — recall the lessons by walking the taxonomy
This replaces preflight's flat recall.

**The commands are the primary leg:**

```bash
regin memory index-root --session "$SID"            # buckets + blurbs
regin memory index-expand <node> --session "$SID"   # descend 1–3 the goal touches
regin memory index-fetch  <node> --session "$SID"   # wiki path, source refs, memory ids
```

~0.5s each, they always read the live store, they carry the **blurbs** (the
one-line "what task should drill in here" router text) that a bare directory
listing does not, and they emit the `memory.index.nav` span the step-2 gate
matches directly. `mcp__memory__index_root` / `index_expand` / `index_fetch`
are the same renderer where the memory MCP is loaded.

**The on-disk walk is the fallback** — `.regin/memory/tree/`, one directory per
topic node and one markdown file per memory, walkable with `Glob`/`Read` and
therefore runnable on a harness with no MCP and no CLI. It is a *cache*:
refreshed when a lesson is captured and after `reflect()`, but not by CLI/UI
curation. Before trusting it, check it is not empty —

```bash
ls .regin/memory/tree/*/*/*.md >/dev/null 2>&1 || regin memory export-tree
```

— because an empty tree makes **every** bucket look like a knowledge gap, the
walk return nothing, and the step-2 gate wall you back into a leg that cannot
pass. **`0 mem` everywhere is a stale export, not a knowledge gap.** That state
is real and has shipped (0 files on disk while `index-root` reported 500+
memories); re-export costs under a second, so just check.

When you do walk it: `Glob .regin/memory/tree/*` for buckets, descend into the
1–3 the goal touches (the tree is ≤3–4 levels), and at the leaf the filenames
are `<title-slug>-<memory-id>.md` — that listing IS the address list. `Read`
only the ones that look on-point. An empty bucket dir in an otherwise populated
tree is a real knowledge gap for this goal — note it, don't force a fit. For
the topic narrative, the curated wiki is at `.regin/topics/wiki/<node>.md`.

Then **pair the walk with one flat `recall`** on the goal's central concept
(`regin memory recall "<concept>" --session "$SID"`, or the `mcp__memory__recall`
tool). This is not optional: the walk only surfaces memories filed under the
buckets you actually descended, so a relevant lesson in a bucket you pruned is
*structurally unreachable*. Recall ranks across the whole store and is the only
leg that finds cross-cutting lessons. Its hits come back as a lead plus a
`⋯ +N chars` index; pull the rest of **at most one** with
`regin memory read <id> --part …` (or the `memory_read` tool) — the leads are
enough to judge relevance, and the full bodies are what blow the 1b budget.

**Record every memory id the walk and the recall surfaced** — that is the
*offered* set for this arm (the tree-nav analogue of the `[lesson-id]`s
preflight prints). You will report which you used in step 6; that inclusion is
how the system learns which lessons help.

#### Recall receipt (MANDATORY — this is the anti-skip artifact)
The arm is unenforced discipline and the skip is a documented, measured failure
mode (`REFERENCE.md`). To make it impossible to hide, step 1b must emit a
**recall receipt**: a verbatim block you carry into the step-2 approval
message. Format:

```
RECALL RECEIPT (goal-verified-treenav arm)
- walk:     <bucket> → <child> → <leaf>     (Glob/Read over .regin/memory/tree/)
- offered:  <id8> (<why>); <id8> (<why>); …  (every file the leaf listed)
- recalled: <id8> via recall, score <s>     (the flat-recall pairing + any opened deeper)
- dead-ends: <empty bucket dir, or "none">  (genuine knowledge gaps)
```

An empty receipt is allowed **only** when the tree genuinely dead-ended
(`0 mem` in the right subtree) *and* the trace gate below still shows the tool
calls happened — i.e. you ran the arm and it returned nothing, which is data,
not a skip. A receipt with no node ids and no spans behind it is a fabricated
receipt; that is worse than admitting you skipped.

### 1.5. Refine — prune the roadmap against the real code
**Small goals skip this step.** The refiner exists to prune `goal preflight`'s
high-recall output, and this arm never runs preflight: your roadmap is the
inline gate floor + a table lookup + the 1–3 leaves you already chose by hand.
Below ~5 kept memories with a single target file there is no over-recall left
to prune, and a `goal-refiner` dispatch is a full context prefill spent
curating something you already curated. Run it when the walk surfaced **≥5
memories** or the diff spans **≥2 areas**; otherwise refine inline while you
read the target file.

The combined roadmap (the inline gates +
table-routed skills + tree-nav's lessons) is still **high-recall** — the
convention table maps every file you touch (so a multi-area change
over-includes), and a tree walk can pull a whole leaf's worth of lessons when
only one applies. So open the **1–2 real target files** the goal names and
refine in place:

- **Drop wrong-area noise** from the scaffold (pure-frontend? drop the Python
  skills and the pytest gate).
- **Keep only lessons that apply.** For each tree-nav memory id, keep it *only*
  if it bears on this change as seen in the actual file; drop subtree-neighbour
  matches that merely shared a topic node. Note what you dropped and why.
- **Add what the walk missed.** If a relevant leaf was at `0 mem` or you
  pruned a subtree too early, `recall` to fill the long tail, or put the real
  edit target at the top of the references by hand.
- **Promote concrete violations you can already see** in the file into
  candidate checklist items.

Ground every keep/drop in something you read in the code. **Agent-arm:**
dispatch `goal-refiner` with the goal + the combined raw roadmap + **the
absolute paths of the target files you already identified** — when the
≥5-memories / ≥2-areas bar above is met. Name the paths: the refiner is
budgeted to ≤3 files, and a dispatch that makes it hunt for the target spends
that budget on Glob/Grep instead of on pruning.

### 2. Roadmap — derive the acceptance checklist, get it approved

**STOP — prove the recall arm actually ran (load-bearing gate).** Before you
present anything for approval, verify from the **trace** — not from your own
memory of what you did — that this session emitted memory-nav tool calls. Every
tool call is a span, so the skip is detectable from data: a `tool.Read` or
`tool.Read` naming `.regin/memory/tree` proves the walk (a `tool.Glob` does
not — it records its pattern, never a result, so globbing a tree that was never
exported would pass a gate on a walk that saw nothing), and a
`memory.index.nav` (the `regin memory` walk) or `tool.mcp__memory__recall` /
`…__memory_read` proves the flat-recall pairing. Run the gate:

    regin gate recall-ran --session "$SID"

Pass your **own** session id. It counts this session's tree-read and recall
spans and returns `GATE PASS` only when the count is > 0, else `GATE FAIL`;
exit code 0/1/2 makes it scriptable as a hard stop. The
`mcp__memory__gate(name="recall-ran", session_id="$SID")` tool is the same check
where the memory MCP is loaded. Because `Read` and the `regin memory` commands
are available in every session, `0` is now an unambiguous skip — there is no "the tool wasn't loaded"
defence any more. This is the
same check the verifier re-runs in step 4. (Span fingerprints live in
`lib/trace/span_gates.py`; add a `SpanGate` there to gate another unenforced
step — it surfaces through both this tool and the `regin gate` CLI.)

- **`0` is a wall, exactly like a red machine gate.** Do not present the
  roadmap, do not build. Return to step 1b and walk the tree for real.
- The count must be **consistent with your receipt** — if the receipt claims a
  walk + two `recall`s but the trace shows `0`, the receipt is fabricated
  (or the trace hasn't flushed: if you *did* just walk, wait a few seconds and
  re-query; never proceed on an unproven receipt).
- Paste the gate output **and** the recall receipt into the approval message
  alongside the roadmap. The user's 15-second checkpoint now includes "did the
  arm run" — which is the whole point of this skill, so it must be visible.

From the **refined** roadmap, write **3–8
falsifiable items** (concrete behaviors + edge cases: states at 0 / 1 / N,
filter counts vs the API, empty/loading/error). **Fold in the tree-nav lessons
you kept** and the violations you spotted. **Show the user the full roadmap +
checklist and get a yes** before building. Record which memory ids you folded
in (the *included* set) for step 6.

**Autonomous mode — self-approve.** If the user is not sitting there (overnight
run, batch job, a goal handed off with "just do it"), do **not** block on a
handshake: post the roadmap, the gate output and the receipt as a summary
message and proceed in the same turn. The checkpoint's purpose is a reviewable
artifact, not a wait state — and a blocking wall is the single largest slice of
a long session's wall-clock, since the human's reply arrives whenever it
arrives.

### 3. Build — then STOP
Implement against the roadmap; reuse the reference components and design
tokens. When you believe it is done: **STOP. Do not commit. Do not
self-congratulate.**

**Agent-arm:** dispatch `goal-builder` with the goal + the approved roadmap +
the acceptance checklist + **the absolute paths of the files to edit and of the
reference components to mirror**. It returns the diff, the verbatim gate output
it ran, and a per-item acceptance status; it will not commit or self-grade —
that is by design. Naming the paths is the point: a fresh context that has to
rediscover them re-Globs, re-Greps and re-Reads what you already read, and
across the measured runs 29% of the files a subagent opened had already been
read by its parent in the same session.

### 4. Verify — independent, adversarial
Hand the work to a checker that did **not** build it:

- **Fresh-context reviewer:** `/code-review high`, a new agent, or a second
  CLI process: *"You did
  NOT write this. The branch claims <goal> is done. Assume it is broken. Check
  each acceptance item PASS/FAIL with proof. Find empty/edge states, filter
  counts that don't match, console errors, untested paths."*
- **Machine gates** from the roadmap — run for real, paste output:
  - frontend: the production build, then the browser suite **scoped to the
    specs covering the changed route** plus the repo's responsive/layout spec;
    zero console errors.
  - non-frontend: the repo's own test suite in its **parallel** form, scoped to
    the touched paths, plus its lint/complexity gates; all green.
- **Recall-arm gate (re-checked here):** the verifier re-runs the gate against
  the builder's session — `regin gate recall-ran --session "<the build
  session's id>"`. `GATE FAIL` / `0` spans = the
  arm was never run = **protocol violation**, treated as a DO-NOT-SHIP wall
  regardless of how good the diff looks. A roadmap that arrived without a
  receipt, or whose receipt isn't backed by spans, fails verification on that
  basis alone.
- A failed gate is a **wall**, not a note.

**Agent-arm — split the roles, don't clone them.** `goal-builder` already ran
the gates; a verifier told to "run the machine gates yourself, for real" runs
them again, and three parallel verifiers run them three more times. In the
measured corpus `goal-verifier` alone re-ran the test suite 833 times (7.2h)
and the browser suite 595 times (3.6h) — most of it work the builder had just
done. So:

- **Small goal → one verifier**, dispatched with `GATE OWNER: yes`.
- **Normal/Large → dispatch verifier #1 alone first**, with `GATE OWNER: yes`.
  When it returns, dispatch #2 (and #3 only if the diff spans ≥2 subsystems)
  **in one message, in parallel**, each with `GATE OWNER: no` and #1's verbatim
  gate output pasted in. The serialization is deliberate: it costs one
  round-trip and saves two full suite runs. Dispatching all three at once
  cannot work — #2 and #3 would have nothing to reuse, and a verifier that is
  handed no gate output and no owner flag falls back to running everything.

**Every verifier dispatch carries:** the goal, the acceptance checklist, the
diff, an explicit `GATE OWNER: yes|no` line, the builder's verbatim gate output
(for non-owners), the build session's id (for the recall-arm re-check above),
and **the pre-change gate baseline** you recorded in step 5 — a verifier given
no baseline cannot tell your red test from the repo's, and will either re-run
it under `git stash` or call it a wall.

Any DO-NOT-SHIP is a wall; `UNPROVEN` is not — it is a named gap, and you
decide whether it is worth a second verifier. Read-only: a verifier reports, it
does not fix.

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
Commit (and only now). Reference the goal; note which gates passed.

Then feed the outcome back — **here you pass the tree-nav ids by hand**, since
this arm never ran `goal preflight`, so nothing was auto-recorded as offered:

```bash
SID=$(regin session-id)   # same probe as step 1a; links these writes to the run
regin goal feedback "<goal>" \
  --included <memory-id-you-folded-in> \
  --offered  <memory-id-the-tree-surfaced> \
  --fail "An acceptance item that FAILED, phrased as a transferable RULE" \
  --tag <area, e.g. frontend> --trace-id "$SID"
```

- `--included` reinforces the tree-nav lessons that earned a place in the
  approved roadmap (one per id you folded in).
- `--offered` lists **every memory id the walk and the recall surfaced** for this goal —
  i.e. the exact `offered:` line of your step-1b receipt (the manual
  replacement for preflight's auto-record). It must match the receipt; if the
  receipt was an honest empty dead-end there is nothing to record here, and
  that absence is itself the signal that the subtree is a knowledge gap.
  The unused offered ids decay naturally.
- `--fail` writes each verification failure as a **new lesson** (phrase it as a
  rule, not "what happened"), tagged so the next roadmap recalls it.
- Always pass `--trace-id "$SID"` or new failure-lessons land with
  `source_trace_id = NULL` and can't be traced back to the run.
- **Did your fix make a recalled lesson false?** Then supersede it rather than
  leaving it to mislead future recall — `regin memory supersede <id> --title …
  --body …`. Trigger only on receipt ids the *verified* change invalidated;
  most runs supersede nothing. Rationale in `REFERENCE.md`.

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

**Do not draft a wiki inline** — re-deriving one spawns a tool-using agent that
runs for minutes and dies when the command exits. `--emit` does only the fast,
non-blocking half: `drifted` gets an agent-free stub refresh proposal (the
re-draft runs later server-side); `missing` stays report-only, for the human to
trigger from the topics UI.

Either way the result is a `pending_review` item — **you do not accept it.**
Report what you found and let the human approve, exactly as for the
`goal feedback` lessons above. A clean "No wiki debt" means your change
stranded no wiki — say so and move on.

## Gotchas

- **Don't fuse build and verify in one prompt.** The STOP in step 3 is
  load-bearing — it asks the typo-maker *not* to proofread itself.
- **The reviewer must have fresh context.** A reviewer that watched the build
  inherits the blind spots. Use `/code-review` or a new agent.
- **Don't run `goal preflight` for the bar in this arm.** Its area router was
  retired; the scaffold is the inline gate floor + the `CLAUDE.local.md` table.
  It appears legitimately only in the optional A/B (`REFERENCE.md`).
- **The silent skip is the documented failure mode of this skill.** Invoking
  `goal-verified-treenav` does **not** mean the recall arm ran; an agent can
  load the skill and bypass step 1b entirely, brute-forcing the answer instead.
  That is what the step-2 gate and the verifier re-check exist to catch. If you
  find yourself reaching for `Bash`/`Read` to audit before you have a recall
  receipt, you are about to skip — stop and walk the tree first.
- **An empty tree is a broken cache, not a dead end.** `0 mem` in *every*
  bucket means the on-disk export is stale, not that the store is empty —
  re-run `regin memory export-tree`, or just use the `regin memory index-*`
  commands, which read the live store. (The directory is also absent on a fresh
  clone, or in a worktree whose memory DB is its own empty one — the DB is keyed
  to `project_root`.) Only trust a dead end that the CLI leg confirms; do
  **not** silently fall back to re-deriving from code.
- **`recall` is not optional.** The walk alone cannot reach a lesson filed
  under a bucket you pruned. Skipping the paired flat recall is the documented
  way this arm misses cross-cutting lessons.
- **No memory MCP? That is not an excuse to skip 1b.** The default leg is the
  `regin memory index-*` subcommands and the fallback is `Glob`+`Read` over the
  exported tree — neither needs MCP, and both leave gate spans. A `0`
  gate on such a harness means you skipped, not that the instrument was absent.
  **A `Glob` alone does not count**: a Glob span records only its pattern,
  never a result, so the gate matches a `tool.Read` of a tree file — finish
  the walk by reading a leaf, or use the `regin memory` commands.
- **Tree dead-ends are data.** A right-bucket-but-empty leaf is a genuine
  knowledge gap; record it (it is exactly the kind of thing step 6's `--fail`
  should seed) and fill from code, not from a forced semantic guess.
- **Preflight needs the repo's `.venv`.** Run from repo root with
  `.venv/bin/python`.

## Further reading

`REFERENCE.md` (this skill's directory) — the A/B methodology for comparing
the two recall arms, the measured cost of the walk vs the old MCP legs, the
post-mortem behind the anti-skip gate, and the plugin-namespaced agent names.
Read it when comparing arms or debugging the gate; you do not need it to run
a goal.

**The A/B is research mode, not part of the loop.** Skip it unless you are
explicitly benchmarking recall arms: running both legs roughly doubles step 1's
cost and feeds nothing extra into the bar.
