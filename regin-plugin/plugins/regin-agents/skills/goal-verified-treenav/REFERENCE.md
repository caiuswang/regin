# goal-verified-treenav — background and A/B methodology

Read this only when you are **comparing the two recall arms**, investigating
why the anti-skip gate exists, or reasoning about how the loop compounds. The
procedure itself is entirely in `SKILL.md`; nothing here is needed to run a
goal.

## Why the recall receipt and the span gate exist

The arm is unenforced discipline, and the failure mode is documented, not
hypothetical. In this skill's **first live run** (`6745849c`) the agent invoked
the skill, *skipped the tree-nav/recall leg entirely*, and re-derived from
scratch — over ~116 Bash calls — a root cause the memory already held at recall
score 1.11.

That is why step 1b must emit a receipt and step 2 must check the trace. The
enforcement pattern generalises: **gate an unenforced skill step on the
existence of its tool's trace span.** Every tool call is persisted as a
`session_spans` row, so "did the agent run the arm?" is answerable from data
rather than self-report. Spans cannot be faked by claiming compliance — they
exist only if the calls fired.

Measured effect of running vs skipping the arm (same rule-trigger prompt,
conclusion-truncated at the blocker handoff, N=1 per arm):

| | BEFORE `6745849c` (skipped) | AFTER `5f9f5bac` (ran) |
|---|---|---|
| recall-arm spans | 0 | 8 |
| time to conclusion | 16.4 min | 6.7 min (**2.4× faster**) |
| output tokens | 50,465 | 22,508 (**2.2× fewer**) |

Same correct verdict both times — the skipped run just re-derived from scratch
what recall already held. Caveat: N=1 per arm, so the direction is solid but
the multiple is one sample. To A/B properly, replay 3–5× per arm and compare
medians.

## Why the walk is a filesystem read

The taxonomy is materialised at `.regin/memory/tree/` by
`regin memory export-tree`, refreshed automatically on every memory write.
Walking directories costs measurably less than the old `index_root` /
`index_expand` / `index_fetch` MCP legs, for the same routing information —
the directory names *are* the bucket ids, so `ls` reproduces the index:

| leg | MCP walk | exported tree |
|---|---|---|
| root listing | 717 tok | 43 tok |
| bucket expand | 354 tok | 114 tok |
| leaf listing | 322 tok | 263 tok |
| **walk total** | **1,393** | **420** |

What the filesystem cannot do is **rank**, which is why `SKILL.md` makes the
paired flat `recall` mandatory rather than optional: the walk only surfaces
memories filed under buckets you descended, so a lesson in a pruned bucket is
structurally unreachable. `recall` ranks across the whole store and is the only
leg that finds cross-cutting lessons.

## Comparing the two arms

To A/B on one goal, run **both** recall legs and diff before refining:

- `goal-verified` arm: `goal preflight "<goal>" --with-lessons` → its
  `[lesson-id]` list.
- this arm: the memory-id list from step 1b's walk + paired recall.

Then compare on what actually matters: **(a)** overlap — which ids both arms
found; **(b)** unique hits — what flat recall caught that the walk pruned, and
what the walk surfaced that text-routing missed (the terse/metaphor-goal case);
**(c)** precision after step 1.5 — of each arm's offered set, what fraction
survived refine into the *included* set. The included/offered ratio recorded by
`goal feedback` is the durable signal: over several goals it tells you which
recall arm pins a sharper bar for *which kinds of goal* (nameable-subsystem vs
cross-cutting). Note the arm you ran in the commit message so the trace is
attributable.

## Running from the `regin-agents` plugin

Plugin-shipped agents register namespaced (`regin-agents:goal-builder`, not
bare `goal-builder`), and bare names only resolve when a same-named agent also
exists outside the plugin. In a plugin-only install, dispatch the qualified
`regin-agents:goal-builder` / `regin-agents:goal-refiner` /
`regin-agents:goal-verifier` form so the agent-arm doesn't silently fall back
to inline. If a dispatch errors with "agent type not found", you are mid-session
before they registered — fall back to inline.

## How this compounds

Same closed loop as `goal-verified` — the walk **recalls** past lessons into
the roadmap (front), `goal feedback` **writes** verification failures back and
reinforces the ones that helped (back) — but because the front leg navigates
the *topic taxonomy*, the reinforcement also teaches you which **subtrees** pay
off for which goal-types. The lessons ride the same `lib/memory` store as
`goal-verified` and `send_to_user(type=lesson)`, so the two arms read and write
the **same** memory pool: anything this arm reinforces sharpens the *other*
arm's flat recall too, and vice-versa. You are comparing access paths over one
shared store, not two separate knowledge bases.
