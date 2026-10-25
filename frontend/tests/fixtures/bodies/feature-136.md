# Session Trace 页面过滤以及操作逻辑优化

## Intent

The sessions list (`/trace/sessions`, `SessionsView.vue`) shows eight row
statuses — working · reconnecting · needs you · idle · starting · inactive ·
ended · closed (`STATES` in `frontend/src/utils/sessionStatus.js`) — but its
Status filter offers only `Any / Active / Not active`, one at a time. The
operator cannot ask "show me what needs me plus what is reconnecting", nor
"show me every inactive row" and then settle them: Close is per-row only, so
clearing ten crashed sessions is ten confirms. This feature makes the filter
speak the row's own vocabulary, lets it take several statuses at once, and
adds Close to the multi-select batch bar beside Delete.

目前 Status 过滤只有 Active / Not active 且单选；改为与列表行状态一一对应、可多选，
并在多选时支持批量 Close。

**Non-goals**
- No new status words. The filter reuses `STATES` labels verbatim; no synonym list.
- No server-side change to how a single row's status is derived — the client's
  `statusOfRow` stays the authority on the chip; the server only mirrors it.
- No URL-param filter state (filters stay component state + localStorage).
- No migration of the old `regin_sessions_active` localStorage value: an
  operator with a saved `active` filter comes back to `Any` once.
- No batch "reopen" — a closed row stays closed.
- The facet is NOT a checkbox list (defect #239's verdict on checkbox lists);
  it is the existing chip row, made toggleable.

## Model

```ts
// The ONE list-row status vocabulary — keys of STATES (sessionStatus.js).
type RowStatus =
  | 'working' | 'retrying' | 'awaiting' | 'idle' | 'starting'   // not ended
  | 'inactive'                                                   // not ended, silent
  | 'ended' | 'closed'                                           // status='ended'

const ACTIVE_STATUSES: Set<RowStatus> = {'working','retrying','awaiting','idle'}

// GET /api/sessions — `active=` is REMOVED and replaced by:
type ListQuery = {
  status?: string          // comma list of RowStatus; empty/absent = any
  // …kind, trace_id, q, scope, since, until, repo, tag, workflow, empty unchanged
}

type ListEnvelope = {
  total_count: number
  active_count: number                       // = Σ status_counts[s], s ∈ ACTIVE_STATUSES
  status_counts: Record<RowStatus, number>   // NEW; every key present, 0 when none
  // …items, repo_counts, tag_counts, next cursor unchanged
}

// POST /api/sessions/batch-close            NEW
type BatchCloseReq  = { trace_ids: string[] }       // same validation as batch-delete
type BatchCloseResp = {
  ok: true
  processed: number        // len(trace_ids)
  closed: string[]         // rows that went not-ended → ended/manual
  skipped: string[]        // already status='ended' — left untouched
  missing: string[]        // unknown ids — no-op, not an error
}

// Client state (useSessionListQuery)
type StatusFilter = Set<RowStatus>   // persisted as JSON array under regin_sessions_status
```

Server-side classifier (one function, `listing.py`), mirroring `statusOfRow`:

```
status='ended' ─┬─ ended_reason='manual' ──────────────────────► closed
                └─ otherwise ──────────────────────────────────► ended
not ended ──────┬─ trace_id has an owned runner phase p ───────► PHASE_STATUS[p]
                │    (working|retrying|idle|starting|awaiting; stopping→working;
                │     inactive-stale→inactive; unknown→inactive)
                ├─ last_seen ≥ now − 10 min ───────────────────► working
                └─ otherwise ──────────────────────────────────► inactive
```

Selection is one axis, row closability another — two diagrams:

```
Selection:   empty ──toggle/shift-click/select-all──► some ──clear/Esc/batch-ok──► empty
                                                       │
                                                  reload: prune ids no longer visible

Row (for Close):  closable (status≠'ended') ──close / batch-close──► ended(manual)
                  ended / closed ── not closable; batch-close skips it
```

## Rules

### Status facet (filters popover)

| Input | Result |
|---|---|
| no chip pressed | `Any` chip pressed; `status` param omitted |
| click an unpressed status chip | added to the set; `Any` unpressed; list reloads |
| click a pressed status chip | removed; if the set becomes empty → `Any` |
| click `Any` | set cleared |
| chip order | `STATES` attention order: needs you, reconnecting, working, idle, starting, inactive, ended, closed |
| chip label / glyph | `statusMeta(key).label` + `.glyph` — no other wording |
| chip count | `status_counts[key]` for the filter set **excluding** the status facet itself (what you would get by toggling it), mirroring `repo_counts` |
| 0-count chip | shown, count `0`, dimmed — never hidden (a pressed 0-count chip must stay reachable) |
| chip semantics | OR within the facet; AND with every other facet |
| accessibility | each chip is a `button` with `aria-pressed`; not a checkbox |

### Active-filter chip row (`SessionActiveFilters`)

| Status set | Chip text | × clears |
|---|---|---|
| empty | (no chip) | — |
| 1–2 statuses | `Status: needs you, working` | the whole status set |
| ≥3 statuses | `Status: 3 states` (title lists them) | the whole status set |

### "N active now" pill (TraceView header)

| Pill state | Click |
|---|---|
| status set ≠ ACTIVE_STATUSES | sets status = ACTIVE_STATUSES, reloads |
| status set = ACTIVE_STATUSES | clears the status set |
| activeCount = 0 | pill not rendered (unchanged) |

### Batch bar (SessionToolbar, shown while selection > 0)

| Selected rows | `Close selected` button | `Delete selected` |
|---|---|---|
| ≥1 closable | shown, label `Close N` where N = closable count | shown |
| 0 closable (all ended/closed) | hidden | shown |
| a batch action in flight | both disabled, the running one reads `Closing…` / `Deleting…` | — |

### Batch close flow (client)

| Step | Behaviour |
|---|---|
| probe | `probeAll` over the selected rows that `isActive` (same as batch delete) |
| confirm | title `Close N sessions`; body states N closing, M already ended (skipped), and `batchWarning(live)` when any live process will be stopped; destructive styling iff live.length > 0 |
| cancel | nothing sent, selection kept |
| confirm | `stopFirst(live)`, then `POST /api/sessions/batch-close` with the **closable** ids only |
| `ok` | selection cleared; flash `Closed X sessions` (+ `, Y already ended` when Y>0); reload |
| `ok:false` / HTTP error | flash the error; selection **kept** so the operator can retry |

### Batch close endpoint (server)

| Row state | Write |
|---|---|
| unknown id | none → `missing` |
| status='ended' (natural or manual) | none → `skipped` (never overwrites a natural `ended_reason`) |
| not ended | status='ended', ended_reason='manual', ended_at=now **iff** ended_at is null → `closed` |
| invalid body (not a list / empty / non-string) | 400, same `_batch_trace_ids_error` as batch-delete |
| any exception mid-batch | whole batch rolled back (one transaction), 500 |

### Selection extras

| Gesture | Effect |
|---|---|
| shift-click a row checkbox | toggles every row between the last-clicked row and this one (visible order, across groups) to this row's new state |
| header Select-all | selects the **loaded** rows only; tooltip says so when `hasNext` |
| Escape | clears selection unless a popover is open (unchanged) |
| changing any filter | reload prunes selected ids no longer visible (unchanged) |

## Invariants

- For every row r on screen: `classify(r)` (server) = `statusOfRow(r, liveness)` (client) whenever both read the same `last_seen` against the same clock instant. One vocabulary, one rule, two mirrors.
- `active_count = Σ status_counts[s]` for s ∈ ACTIVE_STATUSES, and `total_count = Σ status_counts[*]` when no status filter is set. (Today `_session_active_clause` counts a runner in `starting` / `inactive-stale` as active while the client does not — this feature removes that drift.)
- With status set S, every returned row's chip ∈ S; `total_count` = Σ status_counts[s] for s ∈ S.
- Batch close never changes a row that was already `status='ended'`, and never deletes trace data.
- A closed row stays closed after a late span lands (ended_at set ⇒ upsert keeps `ended`), exactly as single Close.
- Batch-close is idempotent: re-sending the same ids closes nothing new and reports them as `skipped`.
- The status facet's chip labels are generated from `STATES`; no status word is spelled anywhere else in the facet.

## Acceptance

```gherkin
Feature: Session list status filter and batch close

  Scenario: status facet offers every row status with counts
    Given sessions in states working, awaiting, inactive, ended and closed
    When the operator opens Filters
    Then the Status facet shows Any plus eight chips labelled from STATES
    And each chip's count equals status_counts from /api/sessions for that state
    And the inactive chip count equals the number of rows the list shows once only inactive is pressed

  Scenario: two statuses are OR-ed
    Given the operator presses "needs you" and "inactive"
    Then the request carries status=awaiting,inactive
    And every listed row's status chip reads "needs you" or "inactive"
    And the active-filter row shows "Status: needs you, inactive"

  Scenario: unpressing the last status returns to Any
    Given only "inactive" is pressed
    When the operator clicks "inactive" again
    Then no status param is sent and the Any chip is pressed

  Scenario: status filter survives a reload
    Given "ended" and "closed" are pressed
    When the page is reloaded
    Then both chips are still pressed and the request carries status=ended,closed

  Scenario: unknown status values are dropped server-side
    When GET /api/sessions?status=inactive,bogus
    Then the response is 200 and equals GET /api/sessions?status=inactive

  Scenario: active count matches the active statuses
    Given a regin-launched runner in phase starting with a recent last_seen
    When GET /api/sessions
    Then active_count equals status_counts.working + retrying + awaiting + idle
    And the runner is counted under starting, not active

  Scenario: active-now pill applies the active statuses
    Given 3 sessions are active
    When the operator clicks "3 active now"
    Then the status set is working, reconnecting, needs you, idle
    And clicking the pill again clears it

  Scenario: batch close settles only closable rows
    Given the operator selects 3 inactive rows and 1 ended row
    Then the batch bar shows "Close 3"
    When they confirm
    Then POST /api/sessions/batch-close carries exactly the 3 inactive ids
    And those rows now read "closed" and the ended row still reads "ended"
    And the selection is empty and the flash says "Closed 3 sessions"

  Scenario: batch close hidden when nothing is closable
    Given the operator selects only ended and closed rows
    Then the batch bar shows Delete selected but no Close button

  Scenario: batch close endpoint skips ended rows and reports missing ids
    Given row A is active, row B ended naturally, id C does not exist
    When POST /api/sessions/batch-close {trace_ids:[A,B,C]}
    Then closed=[A], skipped=[B], missing=[C]
    And B keeps its original ended_reason and ended_at

  Scenario: batch close is one transaction
    Given the write for the second id raises
    When POST /api/sessions/batch-close with three ids
    Then the response is 500 and none of the three rows changed

  Scenario: batch close stops live processes first
    Given one selected row has a reachable live process
    When the operator opens batch close
    Then the confirm shows the live-shutdown warning and destructive styling
    And shutdownAll runs before the batch-close request

  Scenario: failed batch close keeps the selection
    Given batch-close returns ok:false
    Then an error flash is shown and the same rows remain selected

  Scenario: shift-click selects a range
    Given rows 1..6 are visible and row 2 was last checked
    When the operator shift-clicks row 5's checkbox
    Then rows 2, 3, 4 and 5 are selected
```

Gates: pytest `tests/trace/test_sessions_list_params.py`, `test_session_active_clause.py` (+ a new batch-close test); Playwright `sessions-range-and-chips`, `sessions-list`, `sessions-grouping`, `live-shutdown-dialog`, `responsive`; vite build + vue bundle engine.
