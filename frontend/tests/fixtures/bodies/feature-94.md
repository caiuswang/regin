Cut from feature #90's survey. The asymmetry was the defect: Tasks ships 25 components, Features 12, Defects 4. Three readings of one loop, one never finished.

Built 2026-09-21 in commit da3f1d2d. This text now describes what stands, not what was proposed.

## What the browser could not reach

Two CLI capabilities had no HTTP route and therefore no control: `merge <dup> --into <keeper>` and `list --with-fix-state`. `web/blueprints/defects.py` named "the merge remedy" as a 409 case in its own docstring, and `store.attribute()` refuses a duplicate symptom by pointing at a command only the terminal could run — a dead end for anyone in the browser. Both now have routes. (`fix-item --clear` is feature #92 and is deliberately still CLI-only.)

## The triage queue

Triage follows Linear's Triage inbox: a queue the operator routes **from**, never a classifier that assigns a parent. A wrong parent inflates one feature's rounds and exonerates another.

The queue is a **scope, not a second reader**. `?scope=triage` runs the statement every other defect list runs, narrowed by one predicate (`store.in_queue()`), so its rows and its `pagination.total` come out of one `listing()` session. Four terminal verbs, one key each:

| Verb | Key | Effect |
|---|---|---|
| Accept | `1` | operator types the feature id; `feature_id` is set; the triage verdict is cleared |
| Mark duplicate | `2` | operator types the canonical defect; **the duplicate row is DELETED**, its suspect paths unioned into the keeper |
| Decline | `3` | `status=closed`, `triage_state='declined'`, optional `triage_reason`; `feature_id` and `missing_check` stay NULL |
| Snooze | `H` | leaves the queue, returns **on new activity** — never on a timer |

`J`/`K` move the cursor; a chord typed into an open field is text, not a command. No mouse and no modal on the common path.

## Data model

Three columns plus a counter on `defects` (migration 0034):

- `triage_state` — NULL (still in the queue) | `'declined'` | `'snoozed'`. A **second axis**, orthogonal to `status`: `status` asks *is this bug still real*, `triage_state* asks *has anyone routed it*. A declined report and a fixed bug are both closed and only one was ever looked at.
- `triage_reason` — the decline's optional reason. It does **not** go in `missing_check`: that is the ledger's only behaviour-changing field, and a report that was never a bug has no check that would have caught it.
- `activity_seq` — writes to the row, counted. Every verb goes through one `store._touch()` that moves `updated_at` **and** this.
- `snooze_watermark` — `activity_seq` at the instant of a snooze.

The queue predicate is `feature_id IS NULL AND status='open' AND (triage_state IS NULL OR (triage_state='snoozed' AND snooze_watermark IS NOT activity_seq))`.

**Why a counter and not `updated_at`.** The first cut used the timestamp as the watermark and was wrong: `_now()` has second resolution, so a merge into a snoozed defect inside the same second read as *no activity* and the row stayed out of the queue. The counter removes the clock from the predicate entirely.

## Invariants

1. Every CLI capability has an HTTP route before it has a control.
2. Triage never infers `feature_id` from suspect paths, reporting session or cost — the operator declares it or it stays NULL.
3. Merging unions the duplicate's suspect paths into the keeper and rewrites neither row's `blamed_event_id`.
4. The queue's own count badge is `pagination.total` from the read that produced the rows — one predicate, one session, so it cannot go stale the way Plane `#8473` did.
5. `declined` and `snoozed` are reserved states carried on the surviving row. **A duplicate is not one of them**: `store.merge` deletes the duplicate, because `close` demands a `missing_check` and writing "duplicate of #17" there would corrupt the only field that changes future behaviour. The audit trail is the activity log.
6. A snooze never bumps `activity_seq`, or it would end in the same breath that began it.

## Deliberately not built

- **The Pipeline tab badge still counts the whole ledger**, not the queue. Tasks and Features count their populations and Defects must mean the same thing in the same strip. The staleness the Gherkin below was written against is closed instead by `invalidatePipelineCounts()`: a tab that writes a row tells the shell, and the numbers are re-read in the same turn.
- No CLI `decline` / `snooze`. Invariant 1 runs CLI-before-UI, not the reverse; adding them is a follow-up, not a gap.
- No detail-page rail. The survey's layout note (spec/body in the main column, metadata in a rail, history grouped and collapsed) stands as guidance for whoever builds it; `DefectDetailView` is unchanged.

```gherkin
Scenario: the badge cannot disagree with the queue
  Given the triage queue renders 10 defects
  Then the count beside the "Triage" heading reads 10
  When the operator accepts one
  Then both the queue and that count read 9 after the same transaction
  And the Pipeline tab badge is re-read in the same turn

Scenario: snoozing waits on an event, never on a clock
  Given a snoozed defect
  When the queue is read any number of times with nothing written to it
  Then it stays out of the queue
  When any store verb touches it — even inside the same second
  Then it returns to the queue

Scenario: declining is not closing
  Given an unattributed defect that was never a bug
  When the operator declines it with a reason
  Then it is closed with feature_id NULL and missing_check NULL
  And the reason is readable on the row as "declined because"
```

## Sequencing, now spent

The original text blocked this on feature #73 shipping, because both edit `DefectsView.vue`. #73 was still `building` when this ran; the work landed anyway without touching #73's filter model — `triage` was added as one more entry in the existing `SCOPES` table and one more branch in `_narrowed`. A later #73 session merging over this should expect exactly that.
