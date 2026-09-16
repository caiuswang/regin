// The switcher's own helpers — ordering, naming, elapsed copy. The status
// VOCABULARY it renders is not defined here: it lives in sessionStatus.js, the
// single place every regin surface reads a session's label, dot and glyph
// from. This module only says which server phase maps onto it and how rows
// sort.
//
// The server publishes an eight-value phase (`_session_phase` in
// web/blueprints/trace/sessions.py) and both the /live card and this switcher
// render it without re-deriving. Eight states is a status *report*; the
// switcher needs a state you can read at a glance from an 8px dot in a rail,
// so they fold into the shared vocabulary's keys — is it moving, does it want
// me, has it gone quiet, is it over, has it not started.

import { parseLocalIso, serverAgeMs } from './sessionActivity.js'
import { statusFromPhase, statusMeta } from './sessionStatus.js'

export function stateOf(row) {
  return statusFromPhase(row?.phase)
}

export function stateMeta(row) {
  return statusMeta(stateOf(row))
}

// "Active now" means moving, wanting you, or alive at the prompt — not merely
// un-ended. `idle` belongs here and `inactive` does not, and the difference is
// who says so: `idle` is a runner regin owns reporting that the agent is
// sitting at the prompt, while `inactive` is only "silent past the stale
// window" — a session abandoned nine hours ago carries that same phase as one
// wedged a minute ago. Listing those as active drowns the ones that are.
export const ACTIVE_STATES = new Set(['working', 'idle', 'awaiting'])

export function isActiveRow(row) {
  return ACTIVE_STATES.has(stateOf(row))
}

export const GROUP_LABEL = { active: 'Active now', recent: 'Recent' }

export function groupOf(row) {
  return isActiveRow(row) ? 'active' : 'recent'
}

// `title` is nullable until the session names itself; the id prefix is what
// every other regin surface falls back to.
//
// The prefix is only ever a PREFIX. Slicing unconditionally is right for the
// 36-char uuid this usually is and wrong for anything shorter — a trace id of
// `manual-gate` came out as `manual-g`, which is not a truncation the reader
// can see, it is a different string that does not resolve.
const ID_PREFIX_LEN = 8

export function rowName(row) {
  if (row?.title) return row.title
  const id = row?.trace_id
  if (!id) return 'session'
  return id.length > ID_PREFIX_LEN ? `${id.slice(0, ID_PREFIX_LEN)}…` : id
}

const HOUR_MS = 3600_000

function _duration(ms) {
  if (ms == null || ms < 0) return '0m'
  if (ms < HOUR_MS) return `${Math.floor(ms / 60000)}m`
  return `${Math.floor(ms / HOUR_MS)}h ${Math.floor((ms % HOUR_MS) / 60000)}m`
}

// Two different questions, and which one applies depends on the state. A
// session that is moving answers "running for"; one that stopped answers "how
// long ago" — counting up from its start after it went quiet would report
// hours of work that never happened.
export function elapsedLabel(row, clock) {
  if (!row) return '—'
  const measuredFrom = ACTIVE_STATES.has(stateOf(row))
    ? (row.last_start_at || row.started_at)
    : row.last_seen
  const ms = serverAgeMs(measuredFrom, clock)
  if (ms == null) return '—'
  return ACTIVE_STATES.has(stateOf(row)) ? _duration(ms) : `${_duration(ms)} ago`
}

// What the preview's meta grid should CALL that number.
export function elapsedKey(row) {
  return ACTIVE_STATES.has(stateOf(row)) ? 'running for' : 'last seen'
}

export function tokensLabel(row) {
  const total = (row?.input_tokens || 0) + (row?.output_tokens || 0)
  if (!total) return '—'
  if (total < 1000) return `${total} tok`
  return `${Math.round(total / 1000)}k tok`
}

// The last span the session emitted — the row's "current activity" line.
export function lastLine(row) {
  const tail = row?.tail || []
  return tail.length ? tail[tail.length - 1].text : ''
}

// The order both surfaces render, and the order the 1–9 hotkeys count in — so
// the rail's number and the palette's number are always the same session.
//
// It mirrors the server's own ranking (`_PRIORITY` in
// web/blueprints/trace/switcher.py) — that table is ordered to fold onto THIS
// one, phase for state, so the two cannot pick different survivors at the
// roster's cap. Re-sorting by recency alone here would undo the server's rank
// at the last step: a session touched two seconds ago would outrank the one
// parked on a question, on the one surface whose job is to surface it.
const STATE_RANK = {
  awaiting: 0, working: 1, idle: 2, starting: 3, inactive: 4, ended: 5, closed: 5,
}

export function orderRows(rows) {
  return [...(rows || [])].sort((a, b) => {
    const rank = STATE_RANK[stateOf(a)] - STATE_RANK[stateOf(b)]
    if (rank) return rank
    const at = parseLocalIso(a.last_seen)?.getTime() ?? 0
    const bt = parseLocalIso(b.last_seen)?.getTime() ?? 0
    return bt - at
  })
}

// 1–9 are hotkeys; a tenth row is still selectable, it just has no number.
export const MAX_HOTKEY = 9

export function hotkeyFor(index) {
  return index < MAX_HOTKEY ? String(index + 1) : ''
}

export function traceHref(row) {
  // The list route is /trace/sessions — a bare /sessions mounts nothing and
  // renders an empty pane.
  return `/trace/sessions/${row.trace_id}`
}
