# Memory curation surfaces

The human-facing controls for curating the store — the reason memory is mutable
at all. Three entry points over one engine: the `/api/memory/*` blueprint, the
Memory dashboard, and the `regin memory` CLI.

## The API — `web/blueprints/memory.py`

`memory_bp` sits behind the global auth gate (distilled session experience is
never on the public allowlist) and exposes the full curate surface:

- **List / inspect**: `GET /api/memory` (paginated list + census),
  `/stats`, `/<id>`, `/<id>/related` (neighbours + supersede chain), `/graph`
  (the `related` edge graph).
- **Recall / reflect**: `POST /api/memory/recall` (the recall probe, also the
  auto-inject hook's warm-embedder path — see **memory-auto-injection**),
  `POST /api/memory/reflect` (run consolidation), `GET /wiki-recalls`.
- **Curate actions**: `POST /api/memory/bulk` (approve / retire / forget /
  restore many), `PATCH /<id>` (edit), `DELETE /<id>` (forget), and the
  single-row `/<id>/approve` · `/retire` · `/restore`.
- **Taxonomy / filing**: `GET /taxonomy` and `/taxonomy/<node_id>`,
  `POST|DELETE /<id>/topics[/<node_id>]` (link / unlink), and
  `POST /link-orphans` — the agentic classifier that files the unfiled.
- **Topics / feedback / exemplars**: `/topics`, `/topic-feedback` and its
  `/<id>/decision` gate, the exemplar add / remove / list routes, and
  `/topic-route-preview` — these back the two feedback loops
  (**memory-topic-route-feedback**, **memory-exemplar-rescore**), not curation
  proper.
- **Sharing**: tree export / import for cross-clone topic sharing.

## The dashboard — `MemoryView.vue`

`frontend/src/views/MemoryView.vue` is a six-tab shell (`Tabs` + `useTabRoute`):

- **Memories** — `MemoryCategoryBar` plus a resizable `MemoryList` /
  `MemoryDetail` rail, with search, scope / sort filters, `PageControls`, and
  bulk actions; the place to approve a `proposed` distill row or fix a memory's
  veracity.
- **Topics** — `MemoryTopics`, plus the two feedback panels
  (`MemoryTopicFeedback`, `TopicRoutePlayground`) that other topics own.
- **Tree** — `MemoryTaxonomy` (tree / graph / detail); unfiled memories appear
  as a synthetic orphan node.
- **Wikis** — `MemoryWikiRecalls` (per-topic wiki read stats).
- **Recall** — an inline recall probe whose `score_kind` badge reads
  rerank / rrf / fts.
- **Doctor** — `MemoryDoctor`: the store census and reflect console, with a
  "Classify with agent" action that drives `link-orphans`.

## The CLI — `cli/commands/memory.py`

`regin memory` (`memory_app`) mirrors the same operations for scripts and
headless runs: `recall` / `recall-for-task`, `list`, `stats`, `reflect`,
`distill`, `approve`, `forget`, `restore`, `supersede`, `remember`,
`retitle` / `backfill-titles`, `link-topics`, `topic-feedback` / `topic-decide`,
the `exemplar-*` family, `eval`, `curate-apply`, and `export-tree` /
`import-tree`.

Proposals arrive here from **memory-distillation-capture** and are curated
further by **memory-consolidation-reflect**; this page is where a human accepts,
edits, retires, or files what those produce.