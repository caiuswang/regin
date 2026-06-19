# Query Exemplars — Contextual Recall Re-Ranking

Status: implemented (opt-in, off by default)
Owner: agent-memory
Flags: `settings.agent_memory.negative_demotion_weight` (demote, default `0.0`),
`positive_boost_weight` (boost, default `0.0`), `exemplar_boost_ceil` (`1.5`)

## Problem

Recall expressed "this memory is (ir)relevant *here*" by **mutating a stored
property**: importance decay (`reflect._decay_chronically_ignored`) and a binary
global suppress bit (`TopicRouteDecision`). Both are crude:

- A property edit is **global** — lowering importance to silence a memory in one
  context also demotes it everywhere it was relevant; nothing lifts a useful
  memory the cross-encoder happens to under-rank for a particular phrasing.
- It is **not self-recovering** — once decayed, a memory stays demoted even when
  a genuinely-matching prompt arrives.
- It **entangles two orthogonal concerns**: *contextual relevance* ("(not)
  useful for this query") and *lifecycle* ("this memory is bad/obsolete
  everywhere"). They have different correct responses (re-rank vs. retire).

## Decision

Keep relevance and lifecycle separate. Express *contextual* relevance — in
**both directions** — as a **non-destructive, query-local re-rank**, and reserve
property edits for *global* badness.

A memory injected then graded a **hard ignore** records the **embedding of the
prompt it fired on** as a *negative exemplar* (`polarity = -1`); one graded
**engaged**, or hand-curated as a useful case, records it as a *positive
exemplar* (`polarity = +1`). Both live in one `memory_exemplars` table. At recall
time a candidate's score is multiplied by

    clamp(1 − w_neg·max_cos(query, negatives) + w_pos·max_cos(query, positives),
          floor, ceil)

so a memory sinks **only for queries near a context where it failed**, is lifted
**only for queries near one where it helped**, surfaces at full strength
elsewhere, and recovers automatically when incoming queries stop resembling its
exemplars. The memory's `importance` is never touched. A score floor
(`store._NEG_DEMOTION_FLOOR`, `0.05`) keeps a demoted memory recallable; the ceil
(`exemplar_boost_ceil`) keeps a boosted one from running away (a rich-get-richer
guard — a positive can reorder, never dominate).

Why key on the query embedding rather than a label (e.g. the `intent` taxonomy):
the 5-way intent buckets are coarse and measured **inert on the dense+rerank
path** (see `docs/agent-memory-intent-routing.md`). The query vector is as
fine-grained as the embedding space and needs no taxonomy to maintain.

### Positive = rescue, not reinforce

The valuable job for a positive is the inverse of a negative: not to reinforce a
memory that already wins (that would loop — boosted → injected → "engaged" by the
referent-overlap *proxy* → boosted harder), but to **rescue a proven-useful
memory the cross-encoder ranks too low to inject**. The ceil and the opt-in,
modest default weight keep the loop in check; manual cases (below) are the
highest-signal positives because a human, not the proxy, is the gate.

## Signal vs. actuator

Exemplars replace the **actuator**, not the **signal**.

- **Signal** — "injected and (did/didn't) help here." Produced by
  `feedback.score_injection_usefulness` (referent overlap vs. post-injection
  spans). **Unchanged and still required**: an exemplar is *built from* an
  engaged / hard-ignore verdict.
- **Actuator** — what the signal drives. Previously *mutate importance* / *flip a
  suppress bit*. Now: *write a query-local exemplar* of the matching sign.

## Building cases by hand

Beyond auto-capture, a human can attach an exemplar directly — the
lowest-risk, highest-signal path:

- **UI** — the *Recall exemplars* panel (`MemoryExemplars.vue`) lists per-memory
  positive/negative counts and has an inline "build a case" form (memory id +
  example query + polarity).
- **API** — `POST /api/memory/exemplars {memory_id|topic_id, query, polarity}`
  and `DELETE` to undo. Manual rows are stamped `source = 'manual'`.
- **CLI** — `regin memory exemplar-add <id> <query> --positive|--negative
  [--topic]` and `regin memory exemplar-rm <id> [--positive|--negative]`.

## Topics

The same mechanism applies to authoritative topic routes (`topic_exemplars`),
but the route gate is binary (banner / no banner), so it uses a **similarity
threshold** rather than a soft multiplier:

- A `fail`-graded prompt → negative. `store.topic_route_suppressed` withholds the
  banner when the query's max cosine to the topic's negatives clears
  `topic_negative_suppress_sim`.
- A `pass`-graded or curated prompt → positive that **protects** the route: a
  query closer to a positive than to any negative is never suppressed — the
  query-local complement to the standing human `allowed` pin (which still
  overrides outright). `TopicInjection` stays as the event log.

## Schema

- `memory_exemplars` — `(memory_id, polarity, source, query, model_id, dim,
  vector, source_session, created_at)`. Per-(memory, model, polarity), each
  polarity capped independently at `negative_max_per_memory` most-recent rows so
  the per-recall kNN stays cheap. Self-creates via `create_all`; index
  `idx_memory_exemplars_memory`.
- `topic_exemplars` — same shape keyed by `topic_id`. Index
  `idx_topic_exemplars_topic`.
- `query` is the **raw prompt** the exemplar was built from. The vector is
  derived from it; storing the text makes a case *inspectable* (the panel shows
  what you labeled) and *individually revertable* (delete one row by id).
- **Migration**: DBs that predate the unification carried `memory_negatives` /
  `topic_negatives` (negatives only). `engine._apply_rename_migrations` renames
  them in place before `create_all`, and `_COLUMN_MIGRATIONS` adds
  `polarity` (DEFAULT `-1`), `source` (DEFAULT `'auto'`), and `query` (NULL) —
  so existing rows survive as negative exemplars with no data move. Rows written
  before the `query` column stay NULL and render as `(query not recorded)`.

## Operability (build · view · reward/punish · revert)

The four workflows a human needs to run the system, not just feed it:

- **Build** — auto-capture from graded verdicts (`feedback` / `apply_topic_relevance`);
  hand-curate in the **topic-route playground** (probe a query → 👍/👎 a routed
  topic) or via `POST /api/memory/exemplars` / `regin memory exemplar-add`.
- **Reward / punish a real route** — the topic-feedback panel's *recent
  injections* list carries each banner's recorded prompt; 👍/👎 there records a
  protecting/suppressing exemplar keyed on the **actual** firing query — the
  query-local complement to the global suppress/allow decision.
- **View** — `GET /api/memory/exemplars/<kind>/<id>` (kind `topic` | `memory`)
  lists each individual case (query text, polarity, source, origin session,
  timestamp); the `ExemplarCaseList.vue` drill-down renders it inline under both
  the playground and the memory panel. CLI: `regin memory exemplar-list`.
- **Revert** — `delete_exemplar(id, kind)` /
  `DELETE /api/memory/exemplars/<kind>/<id>` removes **one** mislabeled case (the
  ✕ on each case row), the fine-grained undo beside the polarity-wide
  `remove_*_exemplars`. CLI: `regin memory exemplar-forget <id>`.

## Guarantees

- **Opt-in**: with both weights at `0`, `feedback` records no exemplars and
  recall is unchanged. Each direction is gated by its own weight.
- **Dense-only**: only the dense/server recall path carries a query embedding;
  the FTS fallback is unaffected.
- **Cold-start safe**: a memory with no exemplars gets no adjustment.
- **Bounded**: floored *and* ceiled; one noisy verdict can neither bury nor
  enthrone a memory. Keep the weights modest (≈`0.3–0.5`).
- **Model-scoped**: exemplars recorded under a different embedding model are
  ignored.

## Code

| Concern | Symbol |
|---|---|
| Re-rank at recall | `store._apply_exemplar_rescore`, `_exemplar_sim_maps`, `_exemplar_similarities` |
| Write exemplars | `store.add_query_exemplars` (+ `add_query_negatives`/`add_query_positives` wrappers), `_trim_exemplars`, `remove_exemplars` |
| Query embedding (shared) | `store._embed_query` |
| Capture verdicts | `feedback._record_exemplars` (in `score_injection_usefulness`) |
| Carry the query | `InjectionEvent.query`, `store.record_injections`, recall hook |
| Topic suppression / protection | `store.topic_route_suppressed`, `topic_exemplar_max_sim`, `add_topic_exemplars` |
| Topic capture | `store.apply_topic_relevance` → `_record_topic_exemplars` (`fail`→neg, `pass`→pos) |
| Curation (counts + add) | `GET/POST/DELETE /api/memory/exemplars` → `MemoryExemplars.vue` |
| Route playground | `POST /api/memory/topic-route-preview` → `store.topic_query_signals` → `TopicRoutePlayground.vue` |
| View cases + revert one | `store.list_topic_exemplars`/`list_memory_exemplars`/`delete_exemplar`, `GET/DELETE /api/memory/exemplars/<kind>/<id>` → `ExemplarCaseList.vue` |
| Reward/punish real route | `MemoryTopicFeedback.vue` recent-injections 👍/👎 (`TopicInjection.query` → `add_topic_exemplars`) |
| CLI | `regin memory exemplar-add` / `-rm` / `-list` / `-forget` |
| Backfill historical | `scripts/backfill_exemplars.py --polarity positive|negative` |

## Open follow-ups

- Retire the legacy topic fail-rate → human-suppress gate once
  `topic_negative_suppress_sim` is calibrated.
- Calibrate the weights against real sessions before defaulting them on (mirror
  the intent-routing `--compare-routing` eval discipline).
- Consider route-time *force-injection* from topic positives (rescue an
  unrouted topic), not just protection from suppression.
