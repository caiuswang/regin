# Topic-Route Query Exemplars — Contextual Route Re-Ranking

Status: implemented (opt-in, off by default)
Owner: agent-memory
Flags: `settings.agent_memory.topic_negative_suppress_sim` (suppress threshold,
default `0.0`), `negative_max_per_memory` (per-(topic, polarity) cap, default `10`)

> **History.** A parallel mechanism once rescored *memory recall* by signed query
> exemplars (`memory_exemplars`, weighted by `negative_demotion_weight` /
> `positive_boost_weight` in `store._apply_exemplar_rescore`). It was **removed**:
> recall is now driven by the dense + cross-encoder rerank stack plus importance
> decay, and lessons are surfaced by the topic-tree walk (`memory-tree-nav`) and
> flat `recall`. Only the **topic-route** exemplars described below remain — they
> gate the `<topic_context>` banner, which has no other query-local signal.

## Problem

A `<topic_context>` banner is routed by keyword match over the authoritative
topic graph. Expressing "this route is (ir)relevant *here*" by mutating a stored
property — a binary global suppress bit (`TopicRouteDecision`) — is crude:

- A suppress bit is **global** — silencing a route in one context also kills it
  everywhere it was relevant.
- It is **not self-recovering** — once suppressed, a route stays suppressed even
  when a genuinely-matching prompt arrives.
- It **entangles two orthogonal concerns**: *contextual relevance* ("(not) useful
  for this query") and *lifecycle* ("this route is wrong everywhere").

## Decision

Express *contextual* route relevance — in **both directions** — as a
**non-destructive, query-local** signal, and reserve the suppress bit for a
standing human decision.

A banner injected then graded a **fail** (`InjectedRelated`) records the
**embedding of the prompt it fired on** as a *negative exemplar* (`polarity = -1`);
one graded **pass**, or hand-curated as a useful case, records it as a *positive
exemplar* (`polarity = +1`). Both live in one `topic_exemplars` table. At route
time:

- `store.topic_route_suppressed` **withholds** the banner when the incoming
  query's max cosine to the topic's negatives clears `topic_negative_suppress_sim`.
- A positive **protects** the route: a query closer to a positive than to any
  negative is never suppressed — the query-local complement to the standing human
  `allowed` pin (`TopicRouteDecision`), which still overrides outright.

So a route is suppressed **only for queries near a context where it failed**,
protected **only for queries near one where it helped**, surfaces normally
elsewhere, and recovers automatically when incoming queries stop resembling its
negatives. The standing route decision is never touched.

Why key on the query embedding rather than a label (e.g. an `intent` taxonomy):
coarse buckets are measured **inert on the dense+rerank path**. The query vector
is as fine-grained as the embedding space and needs no taxonomy to maintain.

## Signal vs. actuator

Exemplars replace the **actuator**, not the **signal**.

- **Signal** — "the banner was (ir)relevant here." Produced by the grader's
  `InjectedRelated` aspect (`store.apply_topic_relevance`). **Unchanged**: an
  exemplar is *built from* a `pass` / `fail` verdict.
- **Actuator** — what the signal drives. Previously *flip a global suppress bit*.
  Now: *write a query-local exemplar* of the matching sign.

## Building cases by hand

Beyond auto-capture, a human can attach an exemplar directly — the lowest-risk,
highest-signal path:

- **UI** — the *topic-route playground* (`TopicRoutePlayground.vue`): probe a
  query, then 👍/👎 a routed topic to record a protecting / suppressing case. The
  topic-feedback panel's *recent injections* list (`MemoryTopicFeedback.vue`)
  carries each banner's recorded prompt; 👍/👎 there records an exemplar keyed on
  the **actual** firing query.
- **API** — `POST /api/memory/exemplars {topic_id, query, polarity}` and `DELETE`
  to undo. Manual rows are stamped `source = 'manual'`.
- **CLI** — `regin memory exemplar-add <topic_id> <query> --positive|--negative`
  and `regin memory exemplar-rm <topic_id> [--positive|--negative]`.

## Schema

- `topic_exemplars` — `(topic_id, polarity, source, query, model_id, dim, vector,
  source_session, created_at)`. Per-(topic, model, polarity), each polarity capped
  independently at `negative_max_per_memory` most-recent rows so the per-route kNN
  stays cheap. Self-creates via `create_all`; index `idx_topic_exemplars_topic`.
- `query` is the **raw prompt** the exemplar was built from. The vector is derived
  from it; storing the text makes a case *inspectable* (the panel shows what you
  labeled) and *individually revertable* (delete one row by id).
- **Migration**: DBs that predate the unification carried `topic_negatives`
  (negatives only). `engine._apply_rename_migrations` renames it in place before
  `create_all`, and `_COLUMN_MIGRATIONS` adds `polarity` (DEFAULT `-1`), `source`
  (DEFAULT `'auto'`), and `query` (NULL) — so existing rows survive as negative
  exemplars with no data move. Rows written before the `query` column stay NULL
  and render as `(query not recorded)`.

## Operability (build · view · reward/punish · revert)

- **Build** — auto-capture from graded verdicts (`apply_topic_relevance`);
  hand-curate in the playground or via `POST /api/memory/exemplars` /
  `regin memory exemplar-add`.
- **Reward / punish a real route** — the topic-feedback panel's *recent
  injections* list; 👍/👎 records a protecting/suppressing exemplar keyed on the
  **actual** firing query — the query-local complement to the global
  suppress/allow decision.
- **View** — `GET /api/memory/exemplars/topic/<topic_id>` lists each individual
  case (query text, polarity, source, origin session, timestamp); the
  `ExemplarCaseList.vue` drill-down renders it inline under the playground. CLI:
  `regin memory exemplar-list <topic_id>`.
- **Revert** — `delete_exemplar(id, 'topic')` /
  `DELETE /api/memory/exemplars/topic/<id>` removes **one** mislabeled case (the ✕
  on each case row), the fine-grained undo beside the polarity-wide
  `remove_topic_exemplars`. CLI: `regin memory exemplar-forget <id>`.

## Guarantees

- **Opt-in**: with `topic_negative_suppress_sim = 0`, no route is suppressed by
  negatives.
- **Dense-only**: only the dense/server route path carries a query embedding; the
  recall hook is model-free and computes route signals server-side.
- **Cold-start safe**: a topic with no exemplars gets no adjustment.
- **Model-scoped**: exemplars recorded under a different embedding model are
  ignored.

## Code

| Concern | Symbol |
|---|---|
| Suppress / protect at route time | `store.topic_route_suppressed`, `topic_exemplar_max_sim`, `topic_query_signals` |
| Write exemplars | `store.add_topic_exemplars` (+ `add_topic_negatives`/`add_topic_positives`), `_write_topic_exemplars`, `_trim_topic_exemplars`, `remove_topic_exemplars` |
| Capture verdicts | `store.apply_topic_relevance` → `_record_topic_exemplars` (`fail`→neg, `pass`→pos) |
| Carry the query | `TopicInjection.query` |
| Curation (add / remove) | `GET/POST/DELETE /api/memory/exemplars` → `MemoryTopicFeedback.vue` |
| Route playground | `POST /api/memory/topic-route-preview` → `store.topic_query_signals` → `TopicRoutePlayground.vue` |
| View cases + revert one | `store.list_topic_exemplars`/`delete_exemplar`, `GET/DELETE /api/memory/exemplars/topic/<id>` → `ExemplarCaseList.vue` |
| CLI | `regin memory exemplar-add` / `-rm` / `-list` / `-forget` |

## Open follow-ups

- Retire the legacy topic fail-rate → human-suppress gate once
  `topic_negative_suppress_sim` is calibrated.
- Calibrate the threshold against real sessions before defaulting it on.
- Consider route-time *force-injection* from topic positives (rescue an unrouted
  topic), not just protection from suppression.
