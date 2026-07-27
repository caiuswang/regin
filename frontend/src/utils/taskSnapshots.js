// Client-side replay of `session.task_list.events` into one snapshot per
// task-write span.
//
// The payload ships events, not snapshots, on purpose: a 53-task-write session
// would otherwise carry 53 near-identical list blobs. Each event is
// `{span_id, timestamp, task_id, subject?, status?, order?}` and an absent
// field means "this span didn't touch it" (`order` is a snapshot span's
// payload position), so the fold mirrors the server's `_apply_task_row`:
// first non-empty subject wins, every set status overwrites, latest order
// overwrites.
//
// A snapshot is the list AS OF that span — never future state.

const PENDING = 'pending'
const DONE = 'completed'
const ACTIVE = 'in_progress'
// TaskUpdate → `deleted` retires a task; it leaves the list the model sees
// rather than lingering as an open item. (The server's `final` snapshot keeps
// the row, which is why the header badge can count one more task than the
// last card.)
const DELETED = 'deleted'

function applyEvent(state, ev) {
  const id = ev?.task_id
  if (id === undefined || id === null) return
  const key = String(id)
  const entry = state.get(key) || { task_id: key, subject: '', status: PENDING }
  if (ev.subject && !entry.subject) entry.subject = ev.subject
  if (ev.status) entry.status = ev.status
  // A snapshot event's `order` is the task's position in the LATEST whole-list
  // payload — overwrite, so the card tracks the order the agent last wrote.
  if (ev.order !== undefined && ev.order !== null) entry.order = ev.order
  state.set(key, entry)
}

// Tasks a snapshot positioned (an `order` from the latest whole-list payload)
// sort by that position — the order the agent last wrote. Unpositioned tasks
// keep the legacy order: numeric task_ids sort numerically; non-digit ids sink
// to the end then sort lexically — same order the server hands the header its
// `final` list in.
function byTaskId(a, b) {
  const na = /^\d+$/.test(a.task_id) ? Number(a.task_id) : Number.MAX_SAFE_INTEGER
  const nb = /^\d+$/.test(b.task_id) ? Number(b.task_id) : Number.MAX_SAFE_INTEGER
  if (na !== nb) return na - nb
  return a.task_id < b.task_id ? -1 : (a.task_id > b.task_id ? 1 : 0)
}

function byPositionThenId(a, b) {
  const oa = a.order ?? null
  const ob = b.order ?? null
  if (oa !== null && ob !== null && oa !== ob) return oa - ob
  if (oa !== null && ob === null) return -1
  if (oa === null && ob !== null) return 1
  return byTaskId(a, b)
}

function snapshotOf(state) {
  const tasks = []
  let done = 0
  let active = 0
  for (const entry of state.values()) {
    if (entry.status === DELETED) continue
    tasks.push({ ...entry })
    if (entry.status === DONE) done++
    else if (entry.status === ACTIVE) active++
  }
  tasks.sort(byPositionThenId)
  return { tasks, done, active, open: tasks.length - done - active }
}

/**
 * Fold an ordered event list into `Map<span_id, snapshot>`.
 * Snapshot: `{tasks: [{task_id, subject, status}], done, active, open}`.
 */
export function taskSnapshotsBySpan(events) {
  const snapshots = new Map()
  if (!Array.isArray(events) || !events.length) return snapshots
  const state = new Map()
  let i = 0
  while (i < events.length) {
    const spanId = events[i].span_id
    // One span can write more than one task; fold its whole run before
    // snapshotting, so the card never shows a half-applied write.
    while (i < events.length && events[i].span_id === spanId) {
      applyEvent(state, events[i])
      i++
    }
    if (spanId) snapshots.set(spanId, snapshotOf(state))
  }
  return snapshots
}

/** `4 done · 1 active · 1 open` — a zero segment is dropped, not printed. */
export function taskSummaryLabel(snapshot) {
  if (!snapshot) return ''
  return [
    snapshot.done ? `${snapshot.done} done` : '',
    snapshot.active ? `${snapshot.active} active` : '',
    snapshot.open ? `${snapshot.open} open` : '',
  ].filter(Boolean).join(' · ')
}
