# A parked question stays answerable until it is answered

Notation: `docs/spec-notation.md`.

## Intent

An agent that calls `AskUserQuestion` stops until a human answers. regin's job is
to make sure that human can always find the question and answer it — from the
browser, at any time, on any of the session's ids. Nothing the operator does to
*quiet* a notification, and no elapsed time, may remove the only surface able to
resolve a live park.

Non-goal: making the banner un-dismissable. Silencing is the operator's call.
Non-goal: expiring a park. A park has no clock (`agent_sdk.runner._await_operator`).

## Types

```
Park          = { trace_id, alias_trace_ids[], span_id, kind, parked_since,
                  questions[], answerable: 'question'|'decision'|None,
                  dismissed: bool }
BlockerFeed   = { blockers: Park[],      # dismissed excluded — the banner's list
                  parked:   { trace_id, since }[] }   # every live park, one row
                                                      # per id it answers to
DemotionInput = { pending_span, session_activity, latest_span_ts }
```

`latest_span_ts` is keyed `(trace_id, agent_id)`.

## State machine — one park

```
            raised
   (none) ─────────────▶ live ──── answered / denied / newer main prompt ──▶ retired
                          │  ▲
              dismiss ×   │  │  re-read of the feed
                          ▼  │
                       silenced ──── answered elsewhere ──▶ retired
```

`silenced` is a property of the *card*, never of the park: the agent is as
blocked in `silenced` as in `live`.

## Decision table — is a PENDING human blocker demoted to `⏹ interrupted`?

| session | same agent wrote a later span | verdict |
|---|---|---|
| ended | — | demote |
| active / silent | no | **keep PENDING** |
| active / silent | yes | demote |

Only the **span stream** answers column 2. `sessions.last_seen` never does: it is
a heartbeat every ingest writer bumps, so it overtakes a park within minutes of
it being raised.

## Decision table — what a surface shows for a live park

| card state | banner | sessions-list marker | `/live/<trace>` answer sheet |
|---|---|---|---|
| never emitted | stub card | awaiting decision | yes |
| live | card | awaiting decision | yes |
| read / acked | card | awaiting decision | yes |
| **dismissed** | **none** | **awaiting decision** | **yes** |
| park retired | none | none | read-only |

## Invariants

- A park that no span has superseded is served `PENDING`, however long it waits.
- `dismissed_at` on a card removes it from `blockers`; it removes nothing else.
- Every id a park answers to appears in `parked` — the `sdk-…` run id and its
  CLI child, because the sessions list shows the child's.
- Dismissed cards never consume the feed's cap.
- A subagent's spans are never evidence about the main loop's park.

## Gherkin

```gherkin
Scenario: a heartbeat does not interrupt a live park
  Given a PENDING AskUserQuestion that is the newest span its agent wrote
  And the session's last_seen was bumped 113 seconds after it
  And the session has been silent for 15 hours
  When the trace is served
  Then the park is PENDING with no interrupt markers

Scenario: a session that moved on still demotes
  Given a PENDING permission.request
  And a later span written by the same agent
  When the trace is served
  Then the park is ERROR with interrupt_source 'stale'

Scenario: dismissing silences the banner and nothing else
  Given a live park with its card on screen
  When the operator dismisses the card
  Then the banner closes
  And the session row still reads "awaiting decision"
  And nothing claims the session resumed
  And /live/<trace> still offers the answer sheet

Scenario: answering in the terminal clears both surfaces
  Given a live park
  When PostToolUse lands the resolved span and dismisses the card
  Then the banner closes and the row stops flagging
```

## Where it lives

`lib/trace/merge.py` (`_blocks_on_a_human`, `_latest_span_ts`) ·
`lib/agent_messages/blockers.py` (`live_blockers(include_dismissed=…)`,
`_cards_for`, `_card_for`) · `web/blueprints/trace/agent_messages.py`
(`/api/agent-messages/blockers`) · `frontend/src/composables/useNotificationCenter.js`
(`parkedTraceIds`, `applyParked`, the re-read on a `resolved` frame).
