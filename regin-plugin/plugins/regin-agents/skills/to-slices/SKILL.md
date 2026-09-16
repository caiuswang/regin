---
name: to-slices
description: Slice a spec into the work-slice table that lives in `features.body` — the first of the two passes that turn a spec into a runnable graph. Use when a feature has a spec and needs its plan. Refuses a spec with no falsifiable acceptance item, and refuses to give one schema change to two slices.
disable-model-invocation: true
---

A feature has a spec. This is the first of two passes that turn it into a
running graph: **slicing** writes the work-slice table into the spec, and
**transcription** (the second pass) turns that table into tasks and edges.
You are the first pass.

There is no plan entity. The table you write **is** the plan, and the spec's
revision history is the record of what was approved — so a slice that is
wrong is fixed by editing the table and re-running transcription, never by
editing a task by hand.

## The refusals come first

Run the gate before you write a word of the table:

```bash
.venv/bin/python cli/regin.py task slice-check --feature <ID>
```

Exit 0 means the spec is sliceable and its existing table parses. Exit 1
prints every reason on stderr. Two refusals are yours to respect:

| Refusal | What it means | What you do |
|---|---|---|
| The spec names no acceptance test | Its acceptance is prose — there is nothing to put in a slice's Tests column | **Stop and report what is missing.** Do not invent test names; a name you made up is a slice verified against your own guess |
| Two slices own one schema change | Two slices both claim a migration | Give the migration to **one** slice and make the others depend on it |

A spec whose acceptance section is a paragraph of "it should feel fast" is
not a spec you slice badly — it is a spec you refuse, naming the section.

## G-SURFACE — before you slice, not after

**还有哪个界面也干这件事？** Ask it out loud, in writing, before the first
row exists. A feature that adds a way to do something almost always lands
beside a surface that already does it — a CLI command next to an API route
next to a Vue view — and a slice table written without that check produces
the second half of a split brain: the new path shipped, the old one left
saying something different.

Do the check as a search, not as a memory:

1. Name the verb the feature adds ("settle a run", "read the command table").
2. Grep for every surface that already performs it — `cli/commands/`,
   `web/` blueprints, `frontend/src/views/`, and any skill prompt that
   instructs an agent to do it by hand.
3. For each hit, decide **in the table**: this slice changes it, or this
   slice is why it no longer exists, or it is explicitly out of scope and the
   spec's Non-goals say so.

Record the answer in the slicing session's report. "No other surface does
this" is a valid answer — an unasked question is not.

## The table

Write it into the spec with `regin feature spec <ID> --file -`. Two things
carry the base and both are load-bearing:

- the heading **`Base: \`<branch>\``** above the table — the branch the
  feature's own work sits on;
- the **`Base` column** — per slice, because the chain-tail slices branch
  from the integration branch rather than from where the feature started.

| Column | Holds |
|---|---|
| `#` | `S<n>` — stable for the life of the feature; it is half the idempotency key |
| `Slice` | its title |
| `Delivers` | what this slice delivers |
| `Schema` | a tick (`yes`) on the one slice that owns the install-wide schema move, blank on every other. Naming a migration in `Delivers` also counts — either way, two owners is a refusal |
| `Spec refs` | the Rules / Acceptance paragraph numbers |
| `Tests` | acceptance test **names**, not descriptions |
| `Depends on` | other slice ids |
| `Base` | branch name. A human fills it when reviewing the spec; a machine cannot derive it |
| `Skill` | optional; defaults to `goal-verified-treenav` |
| `Verify` | optional; defaults to the project's `test` command |

Invariant 3: a slice's base is **never** the repo's default branch unless
the table says so explicitly.

## The chain tail — you write it, every time

Three rows end every feature's table: **integrate → migrate + doctor →
verify**. They are not optional and they are not the transcriber's to add —
transcription reads the table and nothing else, so a tail that is not in the
table is a tail that never runs. Append them as the last thing you write:

```bash
.venv/bin/python cli/regin.py task tail --feature <ID>
```

It prints the three rows already filled in: numbered on after your highest
`S<n>`, the first of them depending on every leaf of your graph, then chained.
Paste them under your rows and re-run the gate.

**Every command in those rows comes from `.regin/commands.json`** — the
project's command table — and none of them is spelled in the skill or in the
transcriber. That is what makes the tail mean the same thing in a project that
runs `npm test` as in one that runs pytest. If the command refuses because the
project has no table, do not type a command you guessed: run
`regin task commands` for a draft, get it agreed, and commit it first. A key
the table sets to `null` comes through as the reason the table gave, so a
project with no e2e suite gets a verify row that *says* it has none.

## Finishing

Re-run `task slice-check --feature <ID>` against the spec you just wrote —
the gate you passed before writing is not the gate that matters. Then report:
the G-SURFACE answer, the slice ids, and which slice (if any) owns the
schema change. Transcription is a separate pass; do not run it here.
