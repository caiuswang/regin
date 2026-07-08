# Memory engagement feedback

The half of the recall loop that learns whether a *speculatively*-injected
memory actually helped — the positive signal that pairs with reflect's negative
decay. It lives in `lib/memory/feedback.py`, and the verdict it stamps is read
back by the decay gate in **memory-consolidation-reflect**.

## The did-it-help verdict (`score_injection_usefulness`)

For each *unscored* injected memory on a finished trace,
`score_injection_usefulness` pulls the memory's **referents** out of its title
and body (`_referents`): backtick-quoted identifiers and path-shaped tokens
(containing `/` or ending in a source extension), lowercased and at least
`_MIN_REFERENT_CHARS` long. Bare English words are excluded on purpose — a
referent has to be concrete enough to prove contact.

The verdict is an **ordering gate**, and that ordering is load-bearing:
`_post_injection_spans` selects only spans whose `start_time` is *after* the
memory's `injection_events.injected_at` (non-PENDING). A referent that shows up
in one of those later spans' `file_path` / `command` / `text` haystack
(`_span_haystack`) means the memory was `engaged`; none means `ignored`. A
memory with no referents at all **abstains** (`no_referents`) — no credit, no
penalty — because there is nothing to test contact against.

## idf weighting

A referent everyone touches (`cli/regin.py`) proves nothing; a rare one proves a
lot. `_engagement_idf` reads the cached `ReferentSessionDF` table
(`referent_session_df`: a corpus session count plus a referent→session-df map)
and `_idf_weight` maps each referent to a specificity weight in [0,1]. In idf
mode an `engaged` verdict requires the summed weight of matched referents to
clear `engagement_idf_min_weight`; it falls back to plain binary contact when
the setting is off, the cache is empty, or the corpus is below `_IDF_MIN_CORPUS`
(20). `rebuild_session_referent_df` recomputes the table (active referent vocab
× injection sessions → per-referent df → full replace).

## What it stamps, and the soft/hard split

`_record_verdict` stamps three columns onto the `injection_events` row exactly
once (`scored_at` is the idempotency guard): `engaged`, `matched`, and
`scored_at`. The `matched` bit is what lets the decay gate distinguish a **soft
ignore** (matched a referent, but only corpus-saturated ones → benefit of the
doubt) from a **hard ignore** (no contact at all). An engaged hit earns a small
importance bump (`_ENGAGED_BONUS`, capped at `_IMPORTANCE_CAP`) and an `engaged`
validation — but only when `reward_importance=True`.

## The reflect-time sweep

`score_pending_sessions` is the densifying sweep reflect runs before decay: it
finds sessions whose unscored injection events are older than
`feedback_lag_minutes` (the lag lets late spans land), scores each with
`reward_importance=False` (validation only, no importance inflation), and shares
one idf scan across the batch. This is what gives `_decay_reason` a dense
engagement signal to read.

Delivery of the hits this loop judges is **memory-auto-injection**; the ranking
behind them is **memory-recall-pipeline**; the topic-banner analog of this loop
(does a routed `<topic_context>` banner fit) is **memory-topic-route-feedback**.