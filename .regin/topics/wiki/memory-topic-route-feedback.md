# Topic-route relevance feedback

The relevance feedback loop wrapped around authoritative-topic routing — the
topic analog of memory engagement feedback. It measures whether a routed
`<topic_context>` banner actually fit the user's goal, and gates any resulting
suppression behind a human. The store-side machinery lives in
`lib/memory/store.py`.

## Record the banner

The `UserPromptSubmit` hook (`hook_manager/handlers/memory_recall.py`) records
each injected `<topic_context>` banner as a `TopicInjection` row via
`store.record_topic_injection` — idempotent per (session, topic), keeping the
routed `query` so a later `fail` verdict can become a topic negative.

## Judge it

The grader's `InjectedRelated` aspect (`config/settings.json` +
`lib/grader/service._maybe_apply_injection_relevance`) judges whether the banner
fit the user's goal and hands its verdict — `satisfied` / `needs_revision` /
`fail` — to `store.apply_topic_relevance`, which stamps it onto the session's
*unscored* topic injections (idempotent via `scored_at`) exactly once. The same
call routes graded queries into signed exemplars (`_record_topic_exemplars`): a
`fail` becomes a suppressing -1, a `pass` a protecting +1.

## Aggregate and propose

`topic_relevance_stats` returns `(fails, total_scored)` for one topic — the
per-prompt count the gate reads — and `topic_relevance_summary` rolls every
topic into `{injections, scored, fails, fail_rate, decision, status}`. A
recurring fail rate over `topic_relevance_min_scored` / `topic_relevance_fail_rate`
only marks a topic `proposed` — it never withholds on its own.

## Human suppression gate

Withholding is human-in-the-loop. `TopicRouteDecision` rows
(`topic_decision` / `topic_decisions` / `set_topic_decision` / `clear_topic_decision`)
carry a standing decision: `suppressed` makes the hook's `_route_topic` withhold
the banner, `allowed` pins it on (and overrides query-local suppression), and no
row means `auto` (routes, re-proposable). `POST /api/memory/topic-feedback/<id>/decision`
sets it and clears any open inbox proposal for the topic.

## Inspect & probe

Two Memory-view panels make the loop legible.
`frontend/src/components/memory/MemoryTopicFeedback.vue` is the summary grid —
one row per topic (`scored` / `fails` / `fail_rate` / `status` / `decision`), a
fail-rate bar that only *proposes*, and the decision buttons (Approve suppress /
Keep routing on a `proposed` row, Suppress on a routing one, Reset to `auto`) that
POST `/api/memory/topic-feedback/<id>/decision`. Below it, a Recent-injections
accordion (fed by `list_topic_injections`) lists each `<topic_context>` block
regin routed — its relevance verdict, prompt, session link, and 👍/👎 Judge
thumbs that POST `/api/memory/exemplars`. A thumb re-lights from the row's
`judged` flag (`_manual_topic_exemplar_polarities`) after reload, and turns amber
when a click stored nothing because no embedder is configured.

Each row's 🔍 emits `inspect` to open
`frontend/src/components/memory/TopicRoutePlayground.vue`, which probes a query
against `web/blueprints/memory.py::api_topic_route_preview` → `_route_preview`
(backed by `store.topic_query_signals` and `route_explain` in `lib.topics.route`):
it shows what the keyword router (`match_topic`) would inject, its suppress
verdict, and which topics' exemplars lean on the query (pos/neg max-cosine,
counts), and its thumbs hand-curate exemplar cases through the same
`/api/memory/exemplars` write.

The query-local suppress/protect primitive this loop writes into is
**memory-exemplar-rescore**; the routing mechanism it wraps is `topic-routing`.