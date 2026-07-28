---
name: memory-tree-nav
description: "Navigate regin's cross-session agent memory by its topic taxonomy instead of blind semantic search. Use when you want to orient in what the project knows about an area, browse memories by subsystem, or do coarse-to-fine recall by reading topic labels and drilling down — not guessing keywords. Triggers: \"what does regin know about X\", \"browse memories for the trace/eval/memory subsystem\", \"find lessons under topic Y\", \"explore the knowledge tree\", or any recall where you'd rather route by structure than by embedding similarity. Backed by the index_root / index_expand / index_fetch walk over the parent_id tree in .regin/topics/topics/, callable as memory MCP tools or as `regin memory index-*` commands."
---

# Memory Tree Navigation

regin's agent memory is mounted on the **approved topic graph** (`.regin/topics/topics/`, one JSON file per topic + `_meta.json`): every memory links to one or more topic nodes, and the nodes form a `parent_id` tree of ~11 top-level buckets (agent-memory, session-trace, eval-grading, rule-engines, webui, …) each fanning out to leaf topics.

This skill walks that tree **coarse-to-fine** — you read node labels/blurbs and decide where to drill — instead of routing a query through embedding cosine. Use it when you'd rather navigate by structure than guess keywords, or to *complement* `recall` when a query feels familiar but semantic search misses.

## Prefer the exported tree for the walk

The taxonomy is also **materialised on disk** at `.regin/memory/tree/` (one
directory per topic node, one `<title-slug>-<id>.md` per memory), refreshed
automatically on lesson capture and after `reflect()` (CLI/UI curation does
not refresh it — re-run `regin memory export-tree` after editing by hand). Walking it with `Glob`/`Read` costs
measurably less than the MCP legs below for the same routing information —
**420 vs 1,393 tokens** on a root→bucket→leaf walk — because the directory
names *are* the bucket ids, so a listing reproduces the index for free.

Use the `index_*` tools below when you want the **blurbs** (the one-line "what
task should drill in here" router text, which the filesystem doesn't carry), or
when the exported tree is missing. Either way, finish with a flat `recall`:
neither walk can reach a memory filed under a bucket you pruned.

## When to use this vs. plain `recall`

- **Use this (tree nav)** to *orient*: "what does regin know about the trace subsystem?", "show me the eval/grading lessons", "browse what's under topic X". You get a map first, then content.
- **Use `recall`** for a *specific* known question where you can phrase a tight query ("playwright stale backend"). It's one shot, semantic.
- **Combine**: nav to the right subtree, then if it's thin, `recall` scoped to fill the long tail. Tree nav is precision-first; `recall` is recall-first.

## Two ways to call the walk

The three steps exist as **MCP tools** and as **CLI commands**, rendered by one
shared implementation (`lib/memory/tree_nav.py`), so they return the same text:

| Step | MCP tool | CLI |
|---|---|---|
| 1 | `mcp__memory__index_root` | `regin memory index-root [--scope repo:regin]` |
| 2 | `mcp__memory__index_expand` | `regin memory index-expand <node-id>` |
| 3 | `mcp__memory__index_fetch` | `regin memory index-fetch <node-id> [--top-k N]` |
| — | `mcp__memory__recall` / `memory_read` | `regin memory recall <query>` / `regin memory read <id> [--part …]` |

The MCP tools are served by the `memory` server (`lib/memory/mcp_server.py`),
which is long-lived per session — a newly added or changed tool only appears
**after the server reloads** (next session or a restart). The CLI needs no such
reload and works on a harness with no MCP at all, which is what keeps this walk
runnable outside Claude Code.

Pass `--session "$(regin session-id)"` to any of the CLI commands when you are
running them as `goal-verified-treenav`'s recall arm: that is what leaves the
span `regin gate recall-ran` counts.

## Workflow (root → expand → fetch)

1. **`index_root()`** — list the top-level buckets, each as `id · label (N sub, M mem)` plus a one-line blurb describing *what task should drill in here*. Read the blurbs; pick the **1-3 buckets** the task touches.

   - A bucket showing `0 mem` is a genuine knowledge gap — that area has no memories yet. Don't force a fit; note it and read code / use gitnexus instead.

2. **`index_expand(node_id)`** — show one node's blurb + subtree memory count, then each **child** as a card with its own counts. Decide: is the answer broad enough to `fetch` here, or do you descend into a specific child? Repeat expand as needed (the tree is shallow, ≤3-4 levels).

3. **`index_fetch(node_id, top_k=10)`** — the leaf step. Returns **addresses, not contents**, so you spend tokens only on what you open: the **wiki path** (`.regin/topics/wiki/<id>.md` — Read it for the narrative), the topic's high-signal **source refs** (path + role; full file map is in the wiki), and its **memories** as importance-ranked `kind · title · id`. Then *you* decide: `Read` the wiki/refs that matter, `recall` the memory you want. It never dumps a wiki body or memory bodies.

All three take an optional `scope` (e.g. `"repo:regin"`) to filter memories to one repo.

### Example

```
index_root()                      # → pick "session-trace (1 sub, 3 mem)"
index_expand("session-trace")     # → see leaf "session-trace-design (3 mem)"
index_fetch("session-trace-design", top_k=3)   # → read the 3 trace lessons + refs
```

## Decision discipline

- The walk **is** a decision tree: each `blurb` is a gate. Don't fetch everything — prune irrelevant subtrees by reading labels, descend only where relevant. This keeps the pull explainable (you can show the path) and cheap.
- If the tree dead-ends (right bucket, but `0 mem`, or a long-tail topic not yet classified), say so and fall back to `recall` — the tree is a router over the flat store, not a replacement.

## Maintaining the tree (optional, for curation tasks)

The tree only helps if memories are linked to nodes and nodes have good blurbs.

- **Link a memory to a topic node**: `store.link_authoritative_topic(memory_id, topic_node_id, source="manual")` (the store is `lib.memory.get_store()`); unlink with `unlink_authoritative_topic`. Links live in the `MemoryAuthoritativeTopic` table (memory DB), keyed by string topic-id across the two-DB bridge.
- **Edit the taxonomy** (add a bucket, set `parent_id`, write a `blurb`): edit the topic's file under `.regin/topics/topics/` and validate with `lib.topics.validation.audit_graph` (must return 0 errors). Keep depth ≤3-4 and top-level buckets ≤~15 — `index_root` is read on every walk, so an inflated top level is a cost.
- **Blurb craft**: a blurb is a *router card*, not a description. Write "what task should drill in here / what's under me", e.g. "改 lint 规则或引擎时进来", not "this is the rule engine module".

## Underlying code

- `lib/topics/tree.py` — `build_tree` / `subtree_ids` / `node_card` (pure tree helpers over the graph dict).
- `lib/memory/store.py::memories_for_topic_subtree` — subtree memory lookup.
- `lib/memory/tree_nav.py` — the shared renderers behind both front-ends.
- `lib/memory/mcp_server.py` / `cli/commands/memory.py` — the MCP and CLI
  entry points that delegate to them.
- `lib/topics/graph_io.py::load_authoritative_graph` — loads the graph the tools walk.
