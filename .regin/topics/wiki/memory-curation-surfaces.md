# Memory curation surfaces

regin's cross-session memory store is mutable on purpose: distillation proposes memories, reflection rewrites and prunes them, recall ranks them, and engagement feedback rescoring nudges their weights. None of that is useful unless a human can step in to approve a proposal, fix a memory whose claim turned out wrong, hand-curate the signals the rankers learn from, and see what a query would actually surface. This topic is the map of those human-facing controls. They come in three coordinated forms that wrap the same engine (`lib/memory/`):

- a Flask JSON API — `web/blueprints/memory.py` (`/api/memory/*`)
- a Vue 3 dashboard — `frontend/src/views/MemoryView.vue` plus `frontend/src/components/memory/*`
- a Typer CLI — `cli/commands/memory.py` (`regin memory …`)

For the engine internals behind these surfaces, see the sibling topics: capture/[distillation](./memory-distillation-capture.md), [reflect/consolidation](./memory-consolidation-reflect.md), [recall pipeline](./memory-recall-pipeline.md), [signed exemplars](./memory-exemplar-rescore.md), and [topic-route feedback](./memory-topic-route-feedback.md). This page covers only the controls.

## The dashboard — `MemoryView.vue`

The view organizes the surface into four tab routes (`useTabRoute`, default `memories`; valid: `memories`, `topics`, `tree`, `recall`). Selecting a memory from any tab jumps back to **Memories** so its detail pane is visible.

- **Memories** — the browse/manage tab. `MemoryCategoryBar` filters by category; `MemoryList` is the scrollable corpus; `MemoryDetail` is the per-memory pane where you read the body, see its tier/state/topics, and act on it (approve a proposed memory, retire or restore, PATCH-edit the text, forget). A resizable split panel (`useResizablePanel`) divides list and detail.
- **Topics** — `MemoryTopics` lists memories grouped by the topic nodes they're linked to; `MemoryTopicFeedback` surfaces topic-route relevance verdicts and the human suppression gate; `TopicRoutePlayground` lets you type a query and preview which topic banner it would route to (the `@inspect` handoff opens a flagged verdict directly in the playground).
- **Tree** — `MemoryTaxonomy` (composed of `TaxonomyTree`, `TaxonomyGraph`, `TaxonomyDetail`) walks the authoritative topic taxonomy from `.regin/topics/topic.json` as a navigable tree — the WebUI mirror of the `index_root`/`index_expand` memory MCP walk. Each node card carries label, router blurb, child/ref counts, subtree memory count, and whether a curated wiki page exists.
- **Recall** — a probe box that runs a live recall query and renders scored hits, plus `MemoryExemplars` (with `ExemplarCaseList`) for hand-curating the signed query exemplars that rescore recall results.

## The API — `web/blueprints/memory.py`

The `memory_bp` blueprint exposes the routes the dashboard calls, all under `/api/memory`:

- **Browse / read**: `GET /api/memory` (list), `GET /api/memory/<id>` (one), `GET /api/memory/<id>/related`, `GET /api/memory/graph` (associative edge graph).
- **Curate a memory**: `POST /api/memory/<id>/approve`, `POST /api/memory/<id>/retire`, `POST /api/memory/<id>/restore`, `PATCH /api/memory/<id>` (edit body/fields), `DELETE /api/memory/<id>` (hard forget), and `POST /api/memory/bulk` for batch actions.
- **Run the engine**: `POST /api/memory/recall` (probe what a query surfaces), `POST /api/memory/reflect` (trigger consolidation from the UI).
- **Taxonomy**: `GET /api/memory/taxonomy` (whole tree in one payload, optional `scope` filter), `GET /api/memory/taxonomy/<node_id>`, `GET /api/memory/topic-nodes`, plus `POST /api/memory/<id>/topics` and `DELETE /api/memory/<id>/topics/<node_id>` to attach/detach a memory to a topic node, and `GET /api/memory/topics` / `GET /api/memory/topics/<topic_id>`.
- **Topic-route feedback**: `GET /api/memory/topic-feedback`, `POST /api/memory/topic-feedback/<topic_id>/decision` (the human suppression gate), `POST /api/memory/topic-route-preview` (the playground probe).
- **Signed exemplars**: `GET/POST/DELETE /api/memory/exemplars` plus `DELETE /api/memory/exemplars/<kind>/<key>` for add/list/remove/forget of the curated recall-rescore cases.

## The CLI — `cli/commands/memory.py`

The `regin memory` Typer app (`memory_app`) mirrors the same controls for terminal and agent use. Its subcommands:

- **Read / probe**: `recall`, `recall-for-task` (coarse-to-fine subtree recall over the taxonomy), `list`, `stats`.
- **Write a memory**: `remember` (capture a lesson directly).
- **Lifecycle**: `approve`, `forget`, `restore`, `supersede` (retire-and-replace, chained via `superseded_by` — the non-destructive alternative to `forget`).
- **Engine runs**: `reflect` (consolidation), `distill` (post-session capture), `eval` (grade recall/quality).
- **Topic linkage**: `link-topics` (attach memories to topic nodes), `topic-feedback`, `topic-decide` (the suppression decision).
- **Exemplars**: `exemplar-add`, `exemplar-rm`, `exemplar-list`, `exemplar-forget`.
- **Bulk curation**: `curate-apply` (apply a curation plan of retag/retire/forget/merge/rewrite actions, with dry-run validation), and `consolidate-skills` (fold memory-derived guidance into deployed skills).

## Where to start

- Reviewing distill proposals or fixing a wrong memory → **Memories** tab / `approve`, `retire`, `supersede`, PATCH route.
- Tuning what recall ranks → **Recall** tab + `MemoryExemplars` / `exemplar-*` commands and the `exemplars` routes.
- Withholding an over-eager topic banner → **Topics** tab feedback + `topic-decide` / the `topic-feedback/<id>/decision` route.
- Orienting in what the store knows by subsystem → **Tree** tab / `recall-for-task` / the `taxonomy` routes.