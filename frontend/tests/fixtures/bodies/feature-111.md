Intent: one loop a reader can hold in their head — feature → tasks → runs → defects → fix tasks → back to the feature — where every figure is a query over declared facts.

## Two axes, two diagrams

Feature lifecycle (last `feature_events` row decides; order by id):

    specced ──run starts──▶ build_started ──operator ship──▶ shipped
       ▲                                                        │
       └──────── tracker back to backlog ◀──────────────────────┤
    restarted ◀──non-spec run starts / tracker leaves Done──────┘
    any ──operator abandon──▶ abandoned ──run starts──▶ build_started

Defect lifecycle (orthogonal):

    open ──close --check──▶ closed ──reopen──▶ open (Defect.round += 1)
    open & no feature = in triage ──attribute──▶ open (blamed)
                                  ──decline──▶ closed (triage_state=declined)
                                  ──snooze──▶ held until the row is touched

## Decision tables

| Figure | Rule |
|---|---|
| rounds | 1 + count(`restarted`) |
| stage `escaped` | last event is `shipped` AND a defect's `blamed_event_id` = that event's id |
| escapes (reported) | all-time count of defects blamed against any `shipped` event |
| `Defect.round` | 1 + reopens of one defect; shown as "reopened N×" only when > 1; never summed |
| defects tile | "N open of T", or "all T closed" |

| Writer | Appends |
|---|---|
| operator new / ship / abandon | specced / shipped / abandoned |
| non-spec task run starts | spec→build_started, building→nothing, shipped/escaped→restarted, abandoned→build_started |
| tracker refresh | its state mapped to a kind; leaving completed over shipped/escaped → restarted |

## Invariants
- Filing a defect never walks a feature back; working on it (a fix run) does, and that is the round.
- A pre-ship defect costs no round.
- Triage is the queue of open defects with no feature; its only job is to get a parent named, or to decline or snooze the defect.

## Acceptance (tests)
- test_a_clean_reship_after_a_fixed_escape_reads_shipped
- test_index_and_ledger_agree_on_a_clean_reship
- test_a_tracker_walk_back_from_done_is_a_round
- e2e: the defects figure reads as a tally, not a fraction
- e2e: reporting a defect re-reads the figures above it

Non-goals: auto-closing a defect when its fix task finishes (closing demands the missing check); auto-shipping after a fix round.
