# Signed query exemplars — topic-route suppression & protection

The signed query-exemplar mechanism as it operates over authoritative-topic
routing — the contextual, query-local complement to the global, slow
`importance` decay. Implemented in `lib/memory/store.py` over the
`topic_exemplars` table; design note at `docs/agent-memory-exemplars.md`.

## What an exemplar is

An exemplar is the embedding of a prompt a topic banner was injected on, tagged
`polarity` +1 (protect — a `pass`/curated route) or -1 (suppress — a `fail`
route) and `source` `auto` (feedback-captured) or `manual` (human-curated). Rows
live per (topic, polarity), each polarity trimmed to
`negative_max_per_memory` (`_trim_topic_exemplars`). `add_topic_exemplars`
embeds each distinct query once (preferring the embedder's `embed_queries`
extension), writes one `TopicExemplar` per (topic, query), and is a no-op without
an embedder. `add_topic_negatives` / `add_topic_positives` are the polarity-named
wrappers.

## Route-time suppression (`topic_route_suppressed`)

At route time `store.topic_route_suppressed(topic_id, query)` withholds a topic
banner query-locally when the incoming prompt's cosine to that topic's
**negatives** clears `topic_negative_suppress_sim` — *unless* a positive
exemplar is at least as close (`pos < neg` protects) or a standing human
`allowed` decision pins the route on. `topic_exemplar_max_sim` (and its
back-compat `topic_negative_max_sim`) compute the max cosine in [0,1] to a
topic's exemplars of a given polarity under the current model. The effect: a
banner routed-then-judged-irrelevant on similar prompts sinks for *those* queries
only, and one proven relevant is protected — both fading once the exemplars stop
resembling the incoming query, with nothing written to any topic row. Needs the
warm embedder, so it is computed in `/api/memory/recall`, not the model-free
hook.

## Write paths

- **Auto**: `store.apply_topic_relevance` → `_record_topic_exemplars` turns a
  graded `fail` into a -1 negative and a `pass` into a +1 positive (gated on the
  suppression feature being on; best-effort so a write can't fail the grade).
- **Manual**: `POST /api/memory/exemplars` (`web/blueprints/memory.py`) records a
  hand-curated case with `source='manual'`; `frontend/src/components/memory/ExemplarCaseList.vue`
  and the `regin memory exemplar-*` CLI drive it. `remove_topic_exemplars` drops
  a whole polarity; `delete_exemplar` reverts a single mislabeled row;
  `list_topic_exemplars` (via `_exemplar_dict`) is the case list behind the
  drill-down.

## Probe surfaces

`topic_query_signals` / `_topic_query_signal` compute, for a probe query, each
topic's pos/neg max cosine, counts, human `decision`, and the derived
`suppressed` verdict — mirroring `topic_route_suppressed` so the playground shows
exactly what the route hook would do. `_topic_exemplar_counts` reads the
per-polarity counts without loading vectors. `has_embedder` gates whether a
curated case can be stored at all, so the UI can warn before a no-op.

The route-decision half of the same loop (the `InjectedRelated` verdict, the
fail-rate proposal, and the human suppression gate) is
**memory-topic-route-feedback**.