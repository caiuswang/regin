# Task-Scoped Memory Recall — Design Sketch

**Status:** PROPOSED. Nothing here is built yet. Symbols tagged *(exists)* are in the
current tree; everything else is new. Sibling reading: `docs/agent-memory-design.md`
(why the store is decoupled), `docs/agent-memory-exemplars.md` (query-local rescore).

## Problem

Recall fires on the wrong event. The auto-inject hook (`memory_recall.handle`, registered
on `UserPromptSubmit` at priority 90 — *exists*) runs **once, against the raw user goal**.
But a goal isn't one event: it's debug → fix → verify, and each stage wants a *different
branch* of the topic tree. The agent orients once at prompt time, then the navigation
cursor goes stale while the substantive work moves through stages it never re-navigates for.

The obvious fix — re-inject mid-turn — fights the harness: agents don't cleanly re-read
context mid-flight, and the mid-turn `PostToolUse` recall path was scoped but never built
(no `tool_recall.py`, no `inject_on_tool_use` setting). So instead of injecting *into* a
running turn, we **make each stage its own turn**.

## Core idea: stage = task = recall boundary

A task (subagent) is a fresh context with its own prompt boundary. Make each stage a task
and the injection point stops being "mid-turn" (unsupported) and becomes "task start"
(first-class — the same prompt-time recall path we already have, relocated onto a supported
boundary). Two problems dissolve for free:

- **Stage detection disappears.** We don't infer the stage from a fuzzy tool stream — the
  orchestrator *declares* it when it decomposes the goal.
- **The recall query gets dramatically better.** Prompt-time recall keys on the raw goal
  (broad, noisy). Task-time recall keys on the **task description** ("verify the schema
  migration didn't drift `db/schema.sql`") — narrow, stage- and subsystem-specific. Exactly
  the precise query the topic drill-down and exemplar rescore want. Tasks give us good
  recall queries for nothing.

## Practicability check (verified 2026-06-24) — there is no transparent hook path

The appealing version of this design — a `SubagentStart` enrich handler that auto-injects
into every spawned subagent — **does not work**, confirmed two ways:

1. **Harness spec.** Per the official Claude Code hooks docs, `SubagentStart` is a
   *monitoring* event ("track parallel task spawning"); it does **not** accept
   `additionalContext`. The events that can inject context are `UserPromptSubmit`,
   `SessionStart`, `PostToolUse`, and `Stop`. (regin's serializer in `merge.py:215` *would*
   emit `additionalContext` for any event, but the harness ignores it on `SubagentStart`.)
2. **The other candidate — the subagent's own `UserPromptSubmit`** firing regin's existing
   `memory_recall` handler — is empirically dead. In the live trace DB, **1 of 2410
   `memory.recall` spans (0.04%) carries `agent_id`**, i.e. fired inside a subagent — against
   **3,580** `subagent.start` spans over the same period. A subagent's task prompt does not
   reliably go through the `UserPromptSubmit` recall path. So subagents get essentially **no**
   memory today.

**Conclusion:** the only supported, reliable way to put recalled context into a subagent at
spawn time is the one the docs name — **the spawner bakes it into the task prompt.** That
re-introduces a dependency on the *spawner cooperating*; there is no free, transparent,
hook-driven injection for arbitrary `Task` spawns. Plan around that, don't fight it.

## Where injection actually happens: spawner-baked, gate-enforced

### Injection — the spawner calls recall and prepends it to the task prompt

Whatever declares the stages must, before spawning each staged sub-task, call the existing
recall pipeline with `query = task description` routed through `stage ∩ subsystem`, and
prepend the result to the sub-task prompt. Recall is reached via the `recall` MCP tool
(`lib/memory/mcp_server.py:45` — *exists*), `POST /api/memory/recall` on a warm `regin serve`,
or a thin `regin recall` CLI (new, optional). Concrete spawners:

- **A workflow** — a `recallForStage` step before each `agent()` call (sketch below). Fully
  deterministic; the natural home because `pipeline()`/`agent()` already declare the stages.
- **An orchestration skill** (`goal-verified`, `complexity-refactor-tier`, …) — a step in its
  `SKILL.md` that recalls on the sub-task description and includes it in the spawned prompt.
- **Any deliberate parent** — same convention, helped by the CLI/MCP one-liner.

```js
// inside a workflow script — illustrative spawner cooperation
const stages = [
  { stage: 'debug',  subsystem: 'trace',  desc: 'reproduce the span-reparent bug' },
  { stage: 'fix',    subsystem: 'trace',  desc: '...' },
  { stage: 'verify', subsystem: 'trace',  desc: 'confirm merge.py output unchanged' },
]
let carry = []                                  // lessons threaded forward
for (const s of stages) {
  const mem = await recallForStage(s, carry)    // → MCP recall / /api/memory/recall
  const out = await agent(promptWith(s.desc, mem), { label: `${s.stage}:${s.subsystem}` })
  carry = extractLessons(out)                   // seed next stage's query
}
```

There is **no transparent fallback** for spawns whose parent doesn't cooperate. A bare `Task`
call that skips the recall step gets no stage memory — and that is exactly what the gate is
for.

### Enforcement — `SubagentStart` is the gate/trace anchor, not the injector

`SubagentStart` *does* fire reliably (3,580 live spans, current through today) and regin
already traces it (`subagent_lifecycle.handle_start`, `kind='trace'`, emitting `subagent.start`
— *exists*). Its role here is **observability + gating**, not injection: because every spawn
is traced, a span gate can assert "this subagent ran stage X, so a stage-scoped recall span
must exist in its subtree." That turns spawner cooperation from optional discipline into a
wall (see *Per-task span gate* below). The injection happens in the prompt; the proof-it-
happened lives in the trace.

## Stage as an orthogonal facet of the tree

Today topics are subsystem-oriented (trace / eval / memory). Stage is a **second axis** —
per the "keep data-model axes orthogonal" rule, don't overload the subsystem branch with
stage leaves. Two options:

| Option | Shape | Verdict |
|---|---|---|
| **Stage tag on the link** | add `stage` to `MemoryAuthoritativeTopic` (*exists*, `lib/memory/models.py:361`) | Lightweight, but conflates link-provenance with facet |
| **Stage bucket set** | a small parallel set of stage topic nodes (`stage:debug`, `stage:verify`, …) a memory can be linked to alongside its subsystem node | **Preferred** — reuses `link_authoritative_topic(..., source=)` and `memories_for_topic_subtree()` unchanged |

With the bucket set, stage drill-down is just an intersection over existing reads:

```
route(stage_node ∩ subsystem_node) → memories_for_topic_subtree([...]) → leaves
```

`recall()` already accepts `boost_topic_node_id` (*exists*, `store.py:882`) and there's
`memories_for_topic_subtree(node_ids, scope=)` (*exists*) — so the stage facet is a filter/
boost into the current pipeline (FTS → dense → RRF → rerank → quality → topic-boost →
exemplar → MMR), not a new ranker.

## Carry-over between tasks

Without hand-off, staged tasks are isolated and re-derive each other's findings. Each task
returns the lessons it learned; the orchestrator threads them forward as a **seed** to the
next stage's recall query (and persists durable ones via the normal capture path —
`send_to_user(type=lesson)` tee / distill). This is the `carry` variable in (A). It does
double duty: better next-stage query, and new memories written back under the right
stage∩subsystem nodes.

## Per-task span gate

Spawner cooperation is optional discipline unless something enforces it — agent forgets the
recall step before spawning the verify task, verify task gets no memory. Make it a wall.
Extend `lib/trace/span_gates.py` (*exists*; today `RECALL_ARM` matches `tool.mcp__memory__index_%`
and `tool.mcp__memory__recall`) with a task-scoped gate:

```python
TASK_RECALL = SpanGate(
    key="task-recall-ran",
    exact=("memory.recall.task",),
    like=("tool.mcp__memory__index_%",),
    describe="task-scoped recall fired for this stage",
)
```

**Span provenance wrinkle (important).** Because recall runs in the *spawner* (parent), the
recall call lands in the parent's timeline, **not** the subagent's subtree — so the gate can't
just scan the child's spans. Two ways to make it checkable: have the `recallForStage` /
`regin recall --for-task` helper emit a dedicated **`memory.recall.task`** span tagged with the
target stage + task label, and gate at the session level on "one `memory.recall.task` per
declared stage"; or correlate each `subagent.start` (which carries `agent_id`) with a
preceding `memory.recall.task` referencing the same task. The first is simpler and is what the
gate above assumes. Wall condition mirrors `goal-verified-treenav` step 2: `regin gate
task-recall-ran --session <SID>` (*exists* CLI shape, `cli/commands/gate.py`); exit 1 = wall.

## Control flow — a debug → fix → verify goal

1. Orchestrator decomposes the goal into staged tasks (spine declared; tasks may spawn child
   tasks when work forks — every spawn is a fresh recall boundary, at any depth).
2. **Stage `debug`**: spawner recalls on the task desc, routed to `stage:debug ∩ trace`,
   emits `memory.recall.task`, and **prepends** the `<recalled_experience>` to the sub-task
   prompt. Subagent starts already carrying *how debugging this subsystem went before*. Gate
   passes.
3. Subagent returns findings → `carry`.
4. **Stage `verify`**: recall query = task desc + `carry`, routed to `stage:verify ∩ trace`
   — a *different branch* than step 2, which is the whole point. Gate re-checked by the
   verifier.
5. Durable lessons from any stage written back under their stage∩subsystem nodes.

## Reused vs new

| Piece | Status |
|---|---|
| `recall()` pipeline, `boost_topic_node_id`, `memories_for_topic_subtree()` | *exists* — reused as-is |
| `recall` MCP tool / `/api/memory/recall` | *exists* — called per stage |
| `<recalled_experience>` block formatting | *exists* — reused |
| `subagent.start` trace span (`subagent_lifecycle.handle_start`) | *exists* — reused as the gate/correlation anchor |
| `span_gates.py` gate framework + `regin gate` CLI | *exists* — extended with `TASK_RECALL` |
| Stage bucket topic nodes + links | **new** (data, not code) |
| `recallForStage` helper / `regin recall --for-task` + `memory.recall.task` span | **new** |
| Carry-over threading | **new** |
| Settings: `task_recall_top_k` | **new** |
| ~~`SubagentStart` enrich handler~~ | **rejected** — harness ignores `additionalContext` on this event |

## Risks / open questions

- **No transparent injection path (settled).** Verified 2026-06-24: `SubagentStart` can't
  inject (harness spec) and subagent `UserPromptSubmit` recall fires ~never (0.04% in the
  trace DB). Injection *must* be spawner-baked; the gate is the only backstop against an
  uncooperative spawner. This is the central constraint, not a side risk.
- **Cross-boundary span correlation.** The `memory.recall.task` span lives in the parent, the
  work in the child — the gate must correlate them (see *Per-task span gate*). Get this wrong
  and the gate either false-passes or false-walls.
- **Cost.** Every stage = a context window = tokens + orchestration. Split only where stages
  genuinely want *different* memory (debug vs verify do; two consecutive edits don't).
  Gratuitous splitting burns budget and loses continuity.
- **Stage facet authoring.** Who tags a memory with its stage? Bootstrap via `source='route'`
  (keyword match at capture), refine with `'reflect'` synthesis; `'manual'` for curated.
- **Interleaving.** Real debugging re-enters earlier stages. Re-entrant child tasks handle
  this — a debug→fix→debug bounce is just three task spawns, each its own (deduped) recall —
  rather than a stage state machine that has to model the bounce.

---

# Vertical Slice v0 — spec (the smallest end-to-end thing to build)

**Goal of the slice:** prove the whole loop with one spawn — *spawner recalls on a task
description → bakes the result into a sub-task prompt → the recall is provable in the trace →
a gate can assert it ran*. Everything that can be deferred is deferred. If this slice works,
the rest (stage facet, carry-over, multi-stage orchestration) is enrichment on a proven loop.

## Explicitly cut from v0

| Deferred | Why it's safe to cut |
|---|---|
| **Stage facet / stage topic nodes** | Retrieve **structure-first by subsystem only** (pull the subsystem node's subtree). The subsystem axis already fixes relevance; stage becomes a filter-*within*-the-subtree refinement later. No new topic data. |
| **Carry-over between tasks** | v0 is a *single* spawn, not a debug→fix→verify chain. |
| **`SubagentStart` handler** | Rejected outright (harness won't inject); not part of any version. |
| **Auto / transparent injection** | v0 spawner cooperation is **manual** (the agent runs the helper and pastes the block into the `Task` prompt by hand / one Bash line). |
| **New settings** | Reuse `agent_memory.recall_top_k`; add a `--top-k` flag instead of a setting. |

## What v0 builds — three small changes

### 1. `regin recall --for-task` CLI (new command)

A thin wrapper that retrieves **structure-first** (the tree does the retrieving — *not* the
similarity stack with a stage-flavored query), prints the injection block, and leaves a trace
fingerprint. Shipped as `regin memory recall-for-task` (sibling of the existing
`regin memory recall`).

```
regin memory recall-for-task "<task description>" --session <SID> [--subsystem <node-id>] [--top-k 3]
```

Behavior (all reusing existing symbols):
1. **Resolve the subsystem node.** `--subsystem <node-id>` is used directly if it exists in
   the graph; otherwise `match_topic(project_root, task)` (`lib/topics/route.py:329` — *exists*)
   routes the task text to its best topic node. `_merged_graph()` (already in `memory.py`)
   loads the graph.
2. **Structure-first retrieval — NOT similarity.**
   `ids = store.memories_for_topic_subtree(subtree_ids(graph, node_id), scope=scope)`
   (`store.py:834` + `lib/topics/tree.py:subtree_ids` — both *exist*). This returns the
   subsystem subtree's active memories ranked by **importance then recall_count**, with no
   query-similarity in the candidate generation — so a stage-language task description can't
   filter the right memories out of the pool. `scope=None` in v0 (the topic link is the
   filter; scope splitting is a later refinement).
3. **Hydrate + cap.** `hits = [MemoryHit(store.get_dict(i), score, 'topic') for i in ids[:top_k]]`
   (`store.get_dict`, `store.py:451` — *exists*). `_build_block` only reads `hit.memory`, so the
   score is cosmetic (use the memory's importance).
4. Format the `<recalled_experience>` block by reusing `memory_recall._build_block`
   (`hook_manager/handlers/memory_recall.py:96` — *exists*; promote to a shared module later).
5. **Emit the fingerprint span:**
   `post_span(trace_id=session, name='memory.recall.task',
   attributes={'task': task[:120], 'subsystem': node_id, 'hit_count': len(hits), 'hits': [...]})`
   — same `post_span` call `_emit_recall_span` already uses (`memory_recall.py:366`).
6. Print the block to **stdout** (nothing else on stdout, so it pipes cleanly into a prompt).

> **Why structure-first matters:** `store.recall(query, boost_topic_node_id=…)` generates
> candidates by similarity *first* and only boosts topic-linked hits afterward — so memories
> the stage-language query doesn't resemble never enter the pool and the boost can't rescue
> them (and query-expansion to fix this was measured "not worth it"). Pulling the subtree
> directly makes the topic graph the retriever; similarity is demoted to within-set ordering.
> The binding constraint therefore shifts to **topic-link coverage** (`regin memory link-topics`):
> a subsystem node with no linked memories returns nothing — which is correct and measurable.

### 2. `memory.recall.task` span (new name, existing mechanism)

No new infra — it's a second `name=` passed to the same `post_span`. Distinct from the
prompt-time `memory.recall` so the gate and trace projection can tell task-recall from
ordinary recall. (Projection nesting can be ignored in v0 — the span only needs to *exist*
in the session's trace for the gate.)

### 3. `TASK_RECALL` gate (one entry in `span_gates.py`)

```python
TASK_RECALL = SpanGate(
    key="task-recall-ran",
    exact=("memory.recall.task",),
    describe="task-scoped recall fired this session (v0: session-level, not per-stage)",
)
GATES = {g.key: g for g in (RECALL_ARM, TASK_RECALL)}   # append to the existing tuple
```

`regin gate task-recall-ran --session <SID>` then works via the existing `span_count()` +
`cli/commands/gate.py` path — exit 0 if ≥1 `memory.recall.task` span exists for the session,
else 1. **v0 gate is deliberately session-level** ("did any task-recall happen this
session"), *not* per-stage correlation — that's the first enrichment after the slice proves.

## The manual spawn (v0 has no automation)

The "spawner" is you/the orchestrating agent, by hand:

```bash
BLOCK=$(.venv/bin/python cli/regin.py recall --for-task \
  "verify the trace merge output is unchanged" --subsystem trace --session "$SID")
# → paste $BLOCK at the top of the Task/Agent prompt for the verify sub-task
```

That single line *is* the end-to-end proof of "spawner-baked injection."

## Acceptance test (runnable, this is how we know v0 works)

1. **Produces a block + span.** Run the command above against a real `$SID`; stdout is a
   non-empty `<recalled_experience>` block (or an empty-but-valid block if no hits).
2. **Span landed:**
   `sqlite3 db/regin.db "SELECT name, substr(attributes,1,200) FROM session_spans WHERE name='memory.recall.task' AND trace_id='$SID';"`
   → one row, attributes carry `task` + `hits`.
3. **Gate passes:** `regin gate task-recall-ran --session $SID` → exit 0.
4. **Gate walls a skipper:** fresh `$SID2` with no recall → exit 1.
5. **Schema drift check:** no DB schema change in v0 (spans are append-only rows), so nothing
   to fold into `db/schema.sql`.

## The one integration unknown to confirm first

**How does the helper learn the ambient session id?** `post_span` needs `trace_id=<session>`.
v0 passes `--session` explicitly (robust, and a workflow/skill knows its own session id). The
open question is whether the *agent* can read its own session id to pass it — check for an env
var the harness/hook sets, or add a `_resolve_active_session()` fallback (most-recently-active
trace in `db/regin.db`). Resolve this before wiring any non-manual spawner; it does **not**
block the manual acceptance test, which takes `$SID` as a given.

## Build order within the slice

(3) gate + (2) span name are trivial and independent — land them first so the acceptance
check exists. Then (1) the CLI, which is the only real work and is mostly gluing existing
functions. Total new surface: ~one CLI command, one span string, one gate constant.
