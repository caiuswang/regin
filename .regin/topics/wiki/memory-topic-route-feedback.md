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

`list_topic_injections` returns recent injections for the CLI `memory
topic-feedback` and the Memory view's panel, each carrying a `judged`
positive/negative flag (from `_manual_topic_exemplar_polarities`) so a thumb
re-lights after reload. The route playground —
`web/blueprints/memory.py::api_topic_route_preview` → `_route_preview`, backed by
`store.topic_query_signals` and `lib.topics.route` — probes what a query would
route to (keyword `route_explain`) and which topics' exemplars lean on it, and is
rendered by `frontend/src/components/memory/TopicRoutePlayground.vue` /
`MemoryTopicFeedback.vue`.

The query-local suppress/protect primitive this loop writes into is
**memory-exemplar-rescore**; the routing mechanism it wraps is `topic-routing`.