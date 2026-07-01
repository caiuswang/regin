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
| `goal-verified-treenav` (this) | `memory-tree-nav` (`index_root`→`index_expand`→`index_fetch`) | **you walk the topic taxonomy by subsystem**, reading node blurbs as gates, and fetch only the leaves the goal touches (precision-first, explainable path) |

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
| 4 Verify | `goal-verifier` | adversarial, read-only; PASS/FAIL with proof. Run **1–3 in parallel**; any DO-NOT-SHIP is a wall |

**Running from the `regin-agents` plugin instead of this repo:** plugin-shipped
agents register namespaced (`regin-agents:goal-builder`, not bare
`goal-builder`), and bare names only resolve when a same-named agent also
exists outside the plugin. In a plugin-only install, dispatch the qualified
`regin-agents:goal-builder` / `regin-agents:goal-refiner` /
`regin-agents:goal-verifier` form so the agent-arm doesn't silently fall back
to inline.

If a dispatch errors with "agent type not found", you are mid-session before
they registered — fall back to inline. The orchestrator (you) owns the human
checkpoint (step 2), the fix loop, and the commit; never let a worker commit.

## Procedure

### 1a. The deterministic scaffold — gates inline, skills from the table
This arm does **not** run `goal preflight` (its area router was retired — it
only restated the convention table and never generalized to other repos). The
scaffold is fixed and tiny:

- **Hard gates (the universal floor — the loop may NOT exit until both pass):**
  1. the existing test suite stays green;
  2. an independent fresh-context reviewer checked the diff (`/code-review high`).

  Then add the *area's* machine gates from the `CLAUDE.local.md` convention
  table for the files you touch — e.g. `pytest` + radon ≥ C + grit for
  `**/*.py`; `vite build` + Playwright + bundle engines for `**/*.vue`.
- **Convention skills:** read the skills that same table maps to the files you
  will edit *before* writing code (they are backed by rule engines, so reading
  first avoids the round-trip).
- **Reference components:** come from the tree leaf in step 1b (`index_fetch`
  source refs) — mirror them, don't invent new patterns.

Grab this session's id now — the step-2 gate and step-6 feedback both need it:

```bash
SID=$(regin session-id)   # prints $CLAUDE_CODE_SESSION_ID — THIS session's id
```

### 1b. Tree-nav — recall the lessons by walking the taxonomy
This replaces preflight's flat recall. Follow the **`memory-tree-nav`** skill;
the mechanics in brief (the three tools are served by the `memory` MCP server —
if they aren't listed, the server needs a reload; fall back to `goal preflight`
*with* lessons and note that this run is **not** a clean tree-nav arm):

1. **`index_root()`** — read the top-level bucket blurbs; pick the **1–3
   buckets** the goal touches. A bucket at `0 mem` is a real knowledge gap for
   this goal — note it, don't force a fit.
2. **`index_expand(node_id)`** — descend through children, pruning irrelevant
   subtrees by their blurbs. The tree is shallow (≤3–4 levels).
3. **`index_fetch(node_id, top_k=…)`** — at the leaf, get **addresses**: the
   topic wiki path, source refs, and memories as `kind · title · id`. Use
   `scope="repo:regin"` to filter to this repo. Then **`recall`** (or Read the
   wiki) only the memories that look on-point.

**Record every memory id the tree surfaced** — that is the *offered* set for
this arm (the tree-nav analogue of the `[lesson-id]`s preflight prints). You
will report which you used in step 6; that inclusion is how the system learns
which lessons help.

#### Recall receipt (MANDATORY — this is the anti-skip artifact)
The arm is unenforced discipline, and the failure mode is real: in this
skill's **first live run** (`6745849c`) the agent invoked the skill, *skipped
the tree-nav/recall leg entirely*, and re-derived from scratch — over ~116
Bash calls — a root cause the memory already held at recall score 1.11. To
make that skip impossible to hide, step 1b must emit a **recall receipt**: a
verbatim block you carry into the step-2 approval message. Format:

```
RECALL RECEIPT (goal-verified-treenav arm)
- walk:     <bucket> → <child> → <leaf>     (index_root → index_expand → index_fetch)
- offered:  <id8> (<why>, score <s>); <id8> (<why>, score <s>); …
- recalled: <id8> via recall, score <s>     (the ones you opened deeper)
- dead-ends: <bucket@0mem, or "none">       (genuine knowledge gaps)
```

An empty receipt is allowed **only** when the tree genuinely dead-ended
(`0 mem` in the right subtree) *and* the trace gate below still shows the tool
calls happened — i.e. you ran the arm and it returned nothing, which is data,
not a skip. A receipt with no node ids and no spans behind it is a fabricated
receipt; that is worse than admitting you skipped.

### 1.5. Refine — prune the roadmap against the real code (DO NOT SKIP)
Identical to `goal-verified`. The combined roadmap (the inline gates +
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
dispatch `goal-refiner` with the goal + the combined raw roadmap.

### 2. Roadmap — derive the acceptance checklist, get it approved

**STOP — prove the recall arm actually ran (load-bearing gate).** Before you
present anything for approval, verify from the **trace** — not from your own
memory of what you did — that this session emitted memory-nav tool calls. The
memory MCP server logs every `index_*`/`recall` as a span, so the skip is
detectable from data. Call the **`mcp__memory__gate`** tool — the recall-arm
gate is served by the same memory MCP server as the `index_*`/`recall` tools,
so it needs no `regin` CLI:

    mcp__memory__gate(name="recall-ran", session_id="<your $CLAUDE_CODE_SESSION_ID>")

Pass your **own** session id — the memory server is shared and long-lived, so it
cannot infer the caller's session and refuses an empty `session_id`. The tool
counts this session's `tool.mcp__memory__index_*` / `…__recall` spans and
returns `GATE PASS` only when the count is > 0, else `GATE FAIL`. This is the
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

### 3. Build — then STOP
Implement against the roadmap; reuse the reference components and design
tokens. When you believe it is done: **STOP. Do not commit. Do not
self-congratulate.** **Agent-arm:** dispatch `goal-builder`.

### 4. Verify — independent, adversarial
Hand the work to a checker that did **not** build it:

- **Fresh-context reviewer:** `/code-review high`, or a new agent: *"You did
  NOT write this. The branch claims <goal> is done. Assume it is broken. Check
  each acceptance item PASS/FAIL with proof. Find empty/edge states, filter
  counts that don't match, console errors, untested paths."*
- **Machine gates** from the roadmap — run for real, paste output:
  - frontend: `cd frontend && npx vite build` and `… playwright test`; zero
    console errors.
  - python: `.venv/bin/python -m pytest <relevant>`; radon grade ≥ C; grit clean.
- **Recall-arm gate (re-checked here):** the verifier re-runs the gate against
  the builder's session — `mcp__memory__gate(name="recall-ran",
  session_id="<the build session's id>")` (or the `regin gate recall-ran
  --session "$SID"` CLI where regin is installed). `GATE FAIL` / `0` spans = the
  arm was never run = **protocol violation**, treated as a DO-NOT-SHIP wall
  regardless of how good the diff looks. A roadmap that arrived without a
  receipt, or whose receipt isn't backed by spans, fails verification on that
  basis alone.
- A failed gate is a **wall**, not a note.

**Agent-arm:** dispatch `goal-verifier` (1–3 in parallel for non-trivial goals;
any DO-NOT-SHIP is a wall). Read-only — it reports, it does not fix.

### 5. Fix and re-verify
Feed every FAIL back into the build. Re-run step 4. Continue only when every
acceptance item passes and every gate is green.

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
- `--offered` lists **every memory id `index_fetch` surfaced** for this goal —
  i.e. the exact `offered:` line of your step-1b receipt (the manual
  replacement for preflight's auto-record). It must match the receipt; if the
  receipt was an honest empty dead-end there is nothing to record here, and
  that absence is itself the signal that the subtree is a knowledge gap.
  The unused offered ids decay naturally.
- `--fail` writes each verification failure as a **new lesson** (phrase it as a
  rule, not "what happened"), tagged so the next roadmap recalls it.
- Always pass `--trace-id "$SID"` or new failure-lessons land with
  `source_trace_id = NULL` and can't be traced back to the run.
- **Refresh a lesson your fix invalidated (conditional — usually skip).** The
  feedback above only *adds* to memory; it never corrects it. If a memory the
  tree walk surfaced (an id on your step-1b receipt) described a behavior this
  change just made obsolete — a bug you removed, a mechanism you deleted —
  don't leave it to mislead future recall, and don't hard-`forget` it (you'd
  lose the *why*). **Supersede** it: keep the still-true guidance, retire only
  the dead mechanics, cite the commit.
  ```bash
  regin memory supersede <stale-id> \
    --title "<refreshed title>" \
    --body "<kept guidance + 'X removed in <commit>'>"
  ```
  Trigger only on receipt ids the *verified* change made false — most runs
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

## Comparing the two arms (the point of this skill)

To A/B on one goal, run **both** recall legs and diff before refining:

- `goal-verified` arm: `goal preflight "<goal>" --with-lessons` → its
  `[lesson-id]` list.
- this arm: the `index_fetch` memory-id list from step 1b.

Then compare on what actually matters: **(a)** overlap — which ids both arms
found; **(b)** unique hits — what flat recall caught that the walk pruned, and
what the walk surfaced that text-routing missed (the terse/metaphor-goal case);
**(c)** precision after step 1.5 — of each arm's offered set, what fraction
survived refine into the *included* set. The included/offered ratio recorded by
`goal feedback` is the durable signal: over several goals it tells you which
recall arm pins a sharper bar for *which kinds of goal* (nameable-subsystem vs
cross-cutting). Note the arm you ran in the commit message so the trace is
attributable.

## Gotchas

- **Don't fuse build and verify in one prompt.** The STOP in step 3 is
  load-bearing — it asks the typo-maker *not* to proofread itself.
- **The reviewer must have fresh context.** A reviewer that watched the build
  inherits the blind spots. Use `/code-review` or a new agent.
- **Don't run `goal preflight` for the bar in this arm.** Its area router was
  retired; the scaffold is the inline gate floor + the `CLAUDE.local.md` table.
  The only place preflight legitimately appears here is the optional A/B in
  "Comparing the two arms" (`--with-lessons`, to diff the retired flat-recall
  leg against the walk) — a comparison probe, not part of the bar.
- **The silent skip is the documented failure mode of this skill.** Invoking
  `goal-verified-treenav` does **not** mean the recall arm ran — its first live
  run (`6745849c`) proves an agent can load the skill and bypass step 1b
  entirely, brute-forcing the answer instead. That is exactly what the step-2
  span-count gate and the verifier re-check exist to catch. If you find
  yourself reaching for `Bash`/`Read` to audit before you have a recall
  receipt, you are about to skip — stop and walk the tree first.
- **memory MCP tools missing?** The `index_*` tools only appear after the
  `memory` server reloads (next session / restart). If they are genuinely
  absent, the span gate will read `0` — and you must say so explicitly and
  treat this run as a **contaminated arm** (it is not a clean tree-nav run),
  rather than silently proceeding as if you had walked the tree.
- **Tree dead-ends are data.** A right-bucket-but-`0 mem` leaf is a genuine
  knowledge gap; record it (it is exactly the kind of thing step 6's `--fail`
  should seed) and fill from code, not from a forced semantic guess.
- **Preflight needs the repo's `.venv`.** Run from repo root with
  `.venv/bin/python`.

## How this compounds

Same closed loop as `goal-verified` — tree-nav **recalls** past lessons into
the roadmap (front), `goal feedback` **writes** verification failures back +
reinforces the ones that helped (back) — but because the front leg navigates
the *topic taxonomy*, the reinforcement also teaches you which **subtrees** pay
off for which goal-types. The lessons ride the same `lib/memory` store as
`goal-verified` and `send_to_user(type=lesson)`, so the two arms read and write
the **same** memory pool: anything this arm reinforces sharpens the *other*
arm's flat recall too, and vice-versa. You are comparing access paths over one
shared store, not two separate knowledge bases.
