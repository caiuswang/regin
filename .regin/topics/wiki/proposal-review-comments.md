# Proposal review comments

A proposal run produces a draft topic graph plus a wiki. Reviewers discuss that draft in **sidebar feedback threads** rather than editing review prose into the draft body. Each thread is anchored to a specific part of the draft, accumulates comments, carries a resolution state, and — while still open — is replayed into the prompt the next time the draft is regenerated.

This page covers the review-comment surface. The surrounding request → draft → review → apply funnel lives on the sibling topic [topic-proposal-pipeline](./topic-proposal-pipeline.md).

## Data model

`lib/orm/models/proposals.py` defines `ProposalFeedbackThread` and `ProposalFeedbackComment` (mirrored in `db/schema.sql`). A thread belongs to a `ProposalRun` (`run_id`) and pins the `revision_id` it was opened against. Its anchor is two columns: `anchor_kind` plus a JSON `anchor_json` payload. A thread carries a `kind`, a nullable `proposal_topic_id`, optional `quoted_text` (a snapshot of what was commented on), a `created_by`, a `resolution_state`, an `addressed_in_revision_id`, and a JSON `metadata_json`. Comments hang off a thread (`feedback_thread_id`) with an `author_kind` and `body`; the first comment is created together with the thread inside `orm_create_feedback_thread` (`lib/topics/proposal_orm/feedback.py`).

## Thread kinds

The `kind` column distinguishes three producers, and the sidebar renders each differently:

- **`comment`** — a human discussion thread authored in the composer.
- **`review_note`** — an agent-authored automated review note, delegated to `ProposalReviewNoteCard.vue`. It shows an "Automated review" badge plus a recommendation badge read from `thread.metadata.recommendation`: `ACCEPT` (green), `DISMISS` (gray), or `REGENERATE` (yellow), falling back to a blue `REVIEW` label when the field is absent. Its comments render along a left indigo rule. While the note is open (not resolved/dismissed) and the revision is editable it exposes **Regenerate** and **Dismiss** actions; a dismissed note also shows a `dismissed` badge and hides its actions.
- **`content_drift`** — an agent-authored content-drift refresh note (`CONTENT_DRIFT_THREAD_KIND` in `lib/topics/content_drift.py`). Content-drift detection appends one of these onto a topic's origin proposal run when the topic's ref files moved out from under its wiki; the open note rides the regenerate rail so the refresh lands as a new revision on the original proposal. Its body is `_drift_note_body` ("The code under **&lt;topic&gt;** changed since its wiki was last written…") and it is anchored `wiki_range`, so the auto-addressed sweep closes it once the topic's wiki actually changes. The note's `metadata_json` carries the `drifted_paths` that triggered it.

## Anchor kinds

The composer in `ProposalCommentsSidebar.vue` offers a set of anchor options (`anchorOptions`) that map onto `anchor_kind`:

- **`general`** — a review note on the whole revision (`anchor.section = revision-overview`), no `proposal_topic_id`.
- **`proposal_summary`** — the selected topic's whole card (`anchor.section = summary`).
- **`topic_field`** — one field of a topic, `intent` or `aliases` (`anchor.field`).
- **`wiki_range`** — the wiki preview for the selected topic (`anchor.section = wiki-preview`).

The topic-scoped anchors (`proposal_summary`, `topic_field`, `wiki_range`) only appear when a topic is selected; `general` is always available. The anchor kind is what later lets the auto-resolve sweep decide whether the thing a thread points at actually moved.

## API surface

Write routes live in `web/blueprints/topics/proposals.py` under `/api/repos/<name>/topics/proposals/<proposal_id>`:

- `POST …/feedback-threads` — create a thread (and its first comment).
- `POST …/feedback-threads/<id>/resolution` — set resolution state.
- `POST …/feedback-threads/<id>/dismiss-drift` — dismiss a content-drift note as unrelated to the wiki *and* re-baseline the topic's ref digests (see **Not-a-real-drift dismissal** below).
- `POST …/feedback-threads/<id>/comments` — reply on a thread.
- `POST …/feedback-threads/<id>/comments/<comment_id>/update` — edit a comment.
- `POST …/feedback-threads/<id>/comments/<comment_id>/delete` — delete a comment; deleting a thread's last comment removes the whole thread (`{deleted_thread: true}`).

Reads ride along with the proposal-detail endpoint: `GET …/topics/proposals/<proposal_id>` embeds a `feedback_threads` array (via `list_proposal_feedback_threads`) alongside the proposal, wiki, and status payloads.

## Service + ORM layers

`lib/topics/proposals/feedback.py` is a thin service layer: each function validates its arguments, delegates to the matching `orm_*` helper in `lib/topics/proposal_orm/feedback.py`, then writes a topics activity-log record so feedback activity shows up in `regin logs`. Validation rejects an empty `body` and a non-object `anchor` (`_validate_feedback_thread_args`); `set_proposal_feedback_thread_resolution` rejects any state outside `MANUAL_RESOLUTION_STATES`. A thread anchored to a topic first loads the proposal and calls `_find_proposed_topic` so a stale `proposal_topic_id` fails loudly.

The ORM helpers (`orm_create_feedback_thread`, `orm_add_feedback_comment`, `orm_set_feedback_thread_resolution`, `orm_update_feedback_comment`, `orm_delete_feedback_comment`, `orm_list_feedback_threads`, `orm_open_content_drift_threads`, `orm_mark_feedback_threads_addressed`) own the DB writes and scope every lookup to the `(run, repo)` pair. A new thread pins the run's latest revision as its `revision_id`. `_feedback_thread_to_dict` in `lib/topics/proposal_orm/serializers.py` shapes a thread for the UI: it resolves `revision_number` (where the thread was opened) and `addressed_in_revision_number`, and computes an **effective** `resolution_state` via `_effective_resolution_state` — a thread marked `addressed` against a revision *newer* than the one currently being viewed reads back as `open`, so historical revisions show the state as it was then (`stored_resolution_state` carries the raw value). When listing threads for a selected revision, `orm_list_feedback_threads` (through `_fetch_threads_for_revision`) also hides threads opened in revisions newer than the one being viewed.

## Resolution states

`MANUAL_RESOLUTION_STATES = {open, resolved, dismissed}` are the only states a user may set by hand. `addressed` is reserved for the automatic sweep. Reopening a thread (`resolution_state = open`) clears `addressed_in_revision_id` so it counts as live feedback again. Closing a thread deliberately leaves `updated_at` untouched, so resolving does not bump a thread to the top of the `updated_at`-sorted list.

## Carry-forward into regenerate

When a regenerate run is set up, `_resolve_regenerate_inputs_from_proposal` (`lib/topics/proposals/external_jobs.py`) collects the prior draft's threads whose `resolution_state == "open"` and bundles them into the regenerate inputs as `feedback_threads`. `format_review_feedback_for_prompt` (`lib/topics/proposal_drafting.py`) then turns that list into a prompt block ("Review feedback to address in this revision:" followed by each thread's header, quoted text, and comment lines). Only **open** threads steer the next draft — resolved, dismissed, and auto-addressed threads drop out.

## Auto-addressed sweep

When a regenerate lands a new revision, `_mark_addressed_feedback_after_regenerate` (`lib/topics/proposals/external_jobs.py`) calls `orm_mark_feedback_threads_addressed`. The sweep (`_mark_open_threads_addressed`) walks every still-`open` thread, skips any opened in a revision newer than the from-revision (`_is_thread_visible_at_revision`), and asks `_thread_addressed_by_revision` whether the *anchored* content actually changed between the previous and next proposal:

- `topic_field` — compare just that field's snapshot (`_topic_snapshot_value` with the anchor's `field`).
- `proposal_summary` — compare the topic's full snapshot (`_topic_full_snapshot`: label, intent, status, and the list fields aliases, refs, edges, commands, include/exclude globs, evidence paths).
- `wiki_range` — compare the anchored topic's wiki body via `_wiki_range_changed`.

`_wiki_range_changed` diffs the anchored topic's **own** per-topic wiki string (`topic['wiki']`), not the combined run-level wiki blob. This matters for a scoped content-drift regenerate, which rewrites only the drifted topics' sections: diffing the combined blob would flip for every topic at once and spuriously auto-close open `wiki_range` notes on untouched topics. It falls back to comparing the run-level `proposal['wiki']` only when the anchored topic is absent from **both** revisions (a note whose topic was dropped); a topic that is present always carries a per-topic wiki string (the serializer emits `""`, never `None`), so the per-topic diff is authoritative for it.

Threads whose anchored content moved snap to `addressed` with `addressed_in_revision_id` set to the new revision; everything else stays open and rides into the next regenerate. The sweep is a no-op on the first revision (from-revision equals the addressed revision, or there is no prior proposal) and logs a `proposal_feedback_threads_addressed` activity record when it closes any. Because the materiality check gates on a real content change, a byte-identical re-draft leaves an anchored thread open so it re-fires on the next pass rather than being silently marked done.

## Not-a-real-drift dismissal

A `content_drift` note flags that a topic's ref files changed since its wiki was digested. Sometimes that ref edit did not change what the wiki documents. Plain `resolved`/`dismissed` doesn't stick for those: the stored `TopicRefDigest.content_hash` stays stale, so the next `run_content_evolution` pass re-detects the same hash mismatch and — because `emit_refresh_proposal` only skips *open* notes — opens a fresh one, resurrecting the drift forever.

The escape hatch is `dismiss_content_drift_thread` (`lib/topics/proposals/feedback.py`), reached from the `dismiss-drift` route and the sidebar's **"Not a real drift"** button. It only accepts an *open* `content_drift` note: the eligible set comes from `orm_open_content_drift_threads` (open + kind-filtered), which also yields the topic to re-baseline; a resolved note, a plain comment, or an unknown id raises rather than re-fingerprinting the wrong topic. That helper returns `[{run_id, topic_id, thread_id, drifted_paths}]` rows (drifted_paths read back from the thread metadata) and serves two callers: the drift producer passes `proposal_id` + `topic_id` to check idempotently for an existing open note, and the agent-spawn consumer calls it unfiltered to find every origin run still carrying a pending refresh.

The dismissal then delegates to `dismiss_content_drift` (`lib/topics/content_drift.py`), which retires the drift signal on **both** surfaces a drifted topic can live on — the origin-run note and the standalone fallback proposal — in four steps:

1. **Advances the drift baseline** — `capture_ref_digests` re-fingerprints the topic's refs so `detect_drifted_topics` stops flagging them. This is ungated and best-effort so the baseline can be synced to current code regardless of `settings.topic_evolution.evolution_enabled`.
2. **Dismisses every open `content_drift` note for the topic** via `orm_set_feedback_thread_resolution(…, resolution_state="dismissed")`, collecting the dismissed thread ids.
3. **Dismisses the standalone fallback refresh proposal** via `_ignore_standalone_refresh` — for a topic whose drift routed to a `content-drift-<topic>` proposal because `emit_refresh_proposal` found no origin run, this marks that proposal's single topic ignored (`ignore_proposed_topic`). It is a no-op for the common origin-run path, where `load_proposal` misses and it returns `False`.
4. **Clears the inbox card** via `resolve_drift_card`, which resolves the live `wiki-debt` content-drift notification (keyed by `content-drift:<repo>:<topic>`) so a `once`-gated card doesn't linger and a later drift on the same topic can surface a fresh one.

It returns `{topic_id, digests_captured, threads_dismissed, proposal_ignored}` (`threads_dismissed` the dismissed note ids, `proposal_ignored` whether a standalone proposal was dismissed) and writes a `content_drift_dismissed` log record; the service layer additionally writes a `proposal_content_drift_dismissed` activity-log record.

## UI

`ProposalCommentsSidebar.vue` renders the threads. It defaults to the **selected topic** filter (threads with a matching `proposal_topic_id` plus general threads) and can widen to **all** threads; both views sort by `updated_at`. Each thread shows badges for its resolution state (`threadTone`: green when addressed/resolved, gray when dismissed, else yellow), the target label (`topic · field`, `topic · wiki`, `topic · summary`, or `General`), the revision a thread opened in, and, for an addressed thread, the revision it was addressed in. Threads of `kind === 'review_note'` are delegated to `ProposalReviewNoteCard.vue`, whose **Regenerate**/**Dismiss** buttons emit up to the sidebar (`dismiss` maps to `setResolution(id, 'dismissed')`). Ordinary comment threads render inline with reply / resolve / reopen / edit / delete actions; a thread of `kind === 'content_drift'` that is not already resolved/dismissed additionally shows the **"Not a real drift"** button, which POSTs to `dismiss-drift`. Historical (non-latest) revisions render read-only via the `readonly` prop — the composer and per-thread actions are hidden and a notice points the reviewer back to the latest revision.