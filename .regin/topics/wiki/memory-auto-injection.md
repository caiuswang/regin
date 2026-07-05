# Memory auto-injection & on-demand recall

How recalled memories actually reach an agent, and why the delivery path is
shaped the way it is. Two surfaces: the automatic `UserPromptSubmit` hook and
the deliberate `recall` MCP server, joined by one HTTP endpoint.

## The auto-inject hook

`hook_manager/handlers/memory_recall.py` runs as a fresh short-lived process per
prompt, so it cannot load the embedder. Instead it **borrows the warm
`regin serve` embedder** via a loopback-auth-exempt POST to `/api/memory/recall`
(`_recall_via_server`), and cleanly falls back to in-process FTS-only
(`_recall_fts`) when the server is down. It routes the prompt to an
authoritative topic (`_route_topic`) and can withhold a `<topic_context>` banner
it deems suppressed.

Speculative injection never reinforces — it is overlap-gated,
uncalibrated-capped (`_cap_uncalibrated`), and same-session deduped via
`injection_events` (`record_injections`) — except for the earned
`reinforce_resurfaced`, which bumps a memory that keeps mattering exactly once.
Each inject and each routed banner (`record_topic_injection`) is recorded so the
feedback loops can later judge it, and `_emit_recall_span` writes a
`memory.recall` span into the trace so a reviewer sees exactly what was fed to
each prompt.

## The `/api/memory/recall` endpoint

`web/blueprints/memory.py::api_memory_recall` is both the curate-UI recall probe
and the hook's dense path. Its body accepts `query`, `top_k`, `scope`, `mode`,
`min_overlap`, `boost_topic_node_id`, and `route_topic_id`; `_recall_kwargs`
maps those onto `memory.recall`, applying each default. It calls recall with
`reinforce=False` (a probe/speculative surface must not reinforce), then
computes route-time topic suppression **here** — `topic_route_suppressed` needs
the warm embedder, which the model-free hook lacks — and returns
`{hits: [...], topic_suppress}`. Each hit carries its `score` and `score_kind`.
The hook passes the topic it keyword-routed to as `route_topic_id` and withholds
the banner when the server says so.

The endpoint sits behind the global auth gate — memory content is distilled
session experience, so nothing here belongs on the public allowlist; the hook
capture path writes through `lib.memory` directly, not HTTP.

## The recall MCP server

`lib/memory/mcp_server.py` is the long-lived, deliberate deeper-pull path:

- `recall` — mid-task pulls (reinforcing, unlike the speculative hook).
- `index_root` / `index_expand` / `index_fetch` — coarse-to-fine topic-tree
  navigation that returns **addresses** (wiki path, source refs, importance-
  ranked memory titles), not bodies, so navigation stays cheap.
- `gate` — PASS/FAILs a trace-derived span gate (e.g. `recall-ran`) for a
  caller-supplied session id.

Regin imports stay lazy inside each tool call, so server startup and tool
listing survive a DB hiccup. The ranking stack behind every hit is
**memory-recall-pipeline**; the two loops that judge whether a delivered hit or
banner helped are **memory-engagement-feedback** and
**memory-topic-route-feedback**.