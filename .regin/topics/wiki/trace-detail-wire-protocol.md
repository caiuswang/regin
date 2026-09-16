# Trace detail view: the wire protocol

The per-trace view (`/trace/sessions/<id>`) is an **incrementally reconciled mirror** of a server-side projection, not a page that re-downloads what it shows. An open tab re-asks every four seconds, and neither half of that answer is cheap: the projection behind it costs ~130ms before it is memoised (`lib/trace/trace_service/projection_cache.py`), and one prompt's `?deep=1` subtree measured 5.4MB across 1,943 descendants, 97% of it span bodies (`web/app.py`). So the whole design is about answering "has anything changed, and if so which bytes do I not already hold?" as cheaply as possible, without ever letting the browser render something the server no longer believes.

[[session-trace-design]] owns what a span *is*, how it is captured and persisted, and the server-side read stack behind these endpoints — including the writer-invalidated projection memo that produces the epoch this page relies on. [[trace-merge-reconcile]] owns what the serve-time merge decides. This page is only about **what crosses the wire between them, and the arithmetic that makes withholding bytes safe.**

## The three-layer economy

Every optimisation here is one of three moves, and they compose:

1. **Ask fewer questions.** `/map?shallow=1&limit=50` is the reconcile's one unconditional request. `tailFingerprint` (`useTraceData.js`) hashes the identifiers every derived read hangs off — `newest_loaded_id`, `span_count_total`, the four transcript token counters, `active_work_ms`, and the retired-list version. `session_spans` is append-only, so nothing can change under a loaded root without moving one of them. An unchanged fingerprint proves the three follow-ups (`/subtrees`, tool-rollup, `/turn-usage`) cannot return anything new, so a quiet tick costs **one** request instead of six.
2. **Ship each thing once.** A tree node names its span by `span_id` and carries only what is *its own* — expander flags, `child_count`, a group's rolled-up envelope. Bodies ride once in `spans`. `hydrateNodes` rejoins the halves at the client edge, so `node.data` downstream is byte-identical to the pre-split shape.
3. **Don't ship what the client already holds.** Per-root **watermarks** (highest DB row id loaded under that root), a **structure digest** per root, and a **version tag** on the retired-span drop list each let the server answer "you already have this" instead of serialising it.

Layer 3 is the dangerous one, and the rest of this page is why it is safe.

## Withholding a body: the watermark and its licence

`refreshRecentRootSubtrees` asks about the trailing `RELOAD_DEEP_REFRESH_TAIL` roots in **one** `/subtrees` request — one server-side projection for the whole window rather than one per id. Each root may carry `known=<span_id>:<watermark>`. Server-side `_body_needed` withholds a span's body only when all three hold: it is at or below the watermark, it is not named `dirty` by a push frame, and it is settled.

The invariants that make that sound:

- **A new span is an INSERT**, so it takes a fresh row id and is necessarily *above* any watermark the client could have computed. A span the client has never seen can never be withheld.
- **An unfinished span is never withheld**, because its body grows with no identifier moving — the one thing no fingerprint, watermark or digest can see.
- **Watermarks are quoted only for a subtree loaded WHOLE** (`subtreeLoaded`). A lazy expand grafts direct children and no deeper, so its highest child id says nothing about the grandchildren; one sorting below it would be withheld as "already held" when it was never fetched, leaving a hole in the rendered tree.

## The epoch: why a delivered frame is not a delivered stream

The licence to send watermarks is *earned per tick*, and the SSE push stream cannot grant it alone — the fan-out sheds a queue's head under load and re-sends nothing, so receiving a frame is no evidence of having received them all.

So each trace carries an **epoch**: a count of how many times its spans have changed since the server booted, held in process by the projection memo and handed to the browser two ways — the span ingest raises a `trace_spans` frame quoting the epoch and naming the spans it accounts for, and every `/map` carries the authoritative figure as `span_epoch`. Its in-memory lifetime is deliberate: a server restart reads as a gap, which is the correct answer for a client whose peer forgot what it had sent.

The client sends watermarks only while **`frame.epoch − tally == frame.span_ids.length`** on every frame *and* its tally matches `/map`'s figure (`noteSpanFrame` / `noteServerEpoch`). A shed frame, a reconnect, a server restart, or a writer that changed spans without naming them (a reconciler, a materialize reparent, a token attribution) all fail that arithmetic — and an unnamed change *must* fail it, or it would hide inside the next ordinary frame and read as agreement. Any failure sets `resyncNeeded`, and the next refresh asks for full bodies exactly as it would with none of this in place. Rebasing afterwards is what makes the recovery self-healing rather than permanent. `resyncGeneration` exists so a refresh already in flight can tell the resync it was sent to satisfy from one raised while it ran; a failed request must leave the debt standing.

## Versioned drop list and structure digests

`retired_span_ids` is the merge's list of rows the append-only conversation cards must prune. It is deterministic over an append-only window, so once applied it can never say anything new — and on a long session it reached 1544 ids, 37KB, half the response. It therefore carries `retired_digest`: the client echoes back the version it applied as `retired_seen`, a match sends the tag alone, and a version the client has *not* seen always arrives whole so a late retirement still reaches the cards. The fingerprint keys on the **version**, not the list's length — counting a list that is deliberately absent would read as a change on every quiet tick and buy back exactly the requests the fingerprint exists to avoid.

The same argument one level down: each `/subtrees` root carries a structure digest, and a root whose shape is unchanged **and** that has no bodies to deliver is named in `unchanged` rather than serialised again. Shape and content stay separate questions, because a span can grow inside a tree that has not moved. Naming matters: absence from `subtrees` already means "this root has no children", which the client acts on by *clearing* them, and silence cannot carry both answers.

## Lazy and deferred fetches

- **Span bodies** — the shallow map strips `attributes`; `useSpanContentCache` fetches `/spans/<id>/content` per span and overlays the cache onto `session.spans` so every consumer reads one merged list. The overlay merges rather than replaces: serve-time-derived chips (`reclaimed_tokens`, `main_session_impact_tokens`) exist only on the projection and would be erased by a blind replace. It skips cloning spans it is not changing, which is why `attributes` is normalised on the way *into* `session.spans` instead. `force` re-reads a span still being written, since the cache is otherwise write-once and would freeze a mid-stream body at its opening length.
- **Turn usage** — the eager first-open fetch asks for counters only; `span_refs` + `tool_summary` are the turns *sidebar's*, and the sidebar is closed on exactly the load that would pay for them. The rail's first open asks `?detail=1`. `span_count` stays because the sidebar shows it before its own fetch lands, and a turn whose refs have not arrived degrades to the pure-text fallback the sidebar already handled.
- **Session switcher tails** — `?tail=1`, held reference-counted by the ⌘J palette only, so the ambient rail poll carries no preview chat it never renders.
- **Transport** — every trace response is unbounded (that 5.4MB subtree gzips to 1.36MB) and `regin serve` is werkzeug with no compression layer, so Flask-Compress sits on the app factory. `text/event-stream` is deliberately outside its mimetype set: a compressor that waits for the end of a response that never ends would hold every SSE frame forever.

## Poll lifecycle: suspend and wake

`ended_at` is written by the SessionEnd hook, so a session killed, crashed or closed without one has no terminal marker to stop on. Five consecutive unchanged reconciles (`QUIET_TICKS_BEFORE_SUSPEND`) therefore **suspend** the poll — a count of identifiers, not a stopwatch. The floor is the server's, not taste: the fingerprint's transcript half only moves when the live rescan runs, gated at 10s, so under three ticks would read a working agent as quiet.

Suspending is a **pause**, not the retirement an ended session gets, so the way back must exist: the span ingest's `trace_spans` frame wakes it (keyed on the canonical id so a run whose spans land under an alias still reaches the view), as do tab visibility, pull-to-refresh, and the header Refresh. A frame arriving on a *running* poll only resets the count — the `/map`'s own rescan is what causes the ingest, so reloading on it would be a loop with no cadence. A reader parked on a span **still being written** (`presentSpanGrowing`) is exempt from suspending, because its content grows in place; a finished span earns no exemption, and unknown counts as growing so a span rendered from a detached snapshot is unaffected.

## Making the view render correctly — the failure modes

- **Whole trace blank, no error.** Every tree node dropped in `hydrateNodes` because its span did not ride along — the classic cause is a stale `regin serve` still emitting the pre-split wire shape. Silence would read as "empty session", so `hydrateNodes` console-warns once with the drop ratio and says to restart the server. Check that warning first.
- **Duplicate cards beside a resolved span.** The two render paths disagree: the timeline reconciles to `treeNodes` by id, the conversation cards are append-only over `session.spans` by `span_id`. Only `dropRetiredSpans` prunes them, and only when the response actually carried the list — an absent `retired_span_ids` means "the version you already applied", not "nothing to drop".
- **A stale subtree stays expanded.** A root the projection no longer knows (retired placeholder, or reparented between polls) is absent from `subtrees` and must have its children *cleared*, not left alone.
- **A hole in a lazily-expanded tree.** A watermark was quoted for a root that was never loaded whole — see the `subtreeLoaded` gate above.
- **A frozen tail on a live session.** An in-place span rewrite that did not invalidate the server's projection memo, so neither the epoch nor the served bytes moved.
- **The feed reloads while a modal is open.** `useTraceScroll` listens on `document` in the capture phase (scroll does not bubble, and the feed's container varies by layout), so it sees gestures inside an overlay. Target-matching alone is insufficient — where the overlay's box does not overflow, the wheel falls through to the page scroller, which genuinely *is* the feed's. Both affordances are disarmed outright while the reader owns the viewport (`paused`).

Pinned by `frontend/tests/trace-poll-lifecycle.spec.js` (quiet tick, the landing span that buys the full refresh back, suspend, and wake with nobody touching the page), `tests/trace/test_projection_cache.py`, and `tests/web/test_response_compression.py`.