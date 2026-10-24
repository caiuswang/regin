// What a task's schedule says on a row, a card or a lane node — and the
// request bodies the detail card and the bulk bar send. One home, so every
// surface reads "等待时间窗" off the same rule.
import { parseUtc } from './time.js'

/** "in 12m" / "in 3h" / "in 2d" until an ISO moment; "now" once it passed. */
export function untilLabel(iso, now = Date.now()) {
  const d = parseUtc(iso)
  if (!d) return ''
  const mins = Math.round((d.valueOf() - now) / 60000)
  if (mins <= 0) return 'now'
  if (mins < 60) return `in ${mins}m`
  const hours = Math.round(mins / 60)
  return hours < 48 ? `in ${hours}h` : `in ${Math.round(hours / 24)}d`
}

const GATES = ['at', 'window']

/** Armed, unblocked, and held only by a shut gate. */
export function waitingOnGate(task) {
  const s = task?.schedule
  return Boolean(s && s.enabled && GATES.includes(s.kind) && !s.gate_open
    && task.status === 'armed' && !(task.blocked_by || []).length)
}

/** `{ label, tone, title }` for the row chip, or null for no schedule.
 *  `compact` is the lane node: fixed width, so the countdown rides alone —
 *  the prefix is what has to give, never the "多久之后". */
export function scheduleChip(task, compact = false) {
  const s = task?.schedule
  if (!s) return null
  if (!s.enabled) return { label: '定时已关', tone: 'muted', title: s.summary }
  const countdown = untilLabel(s.next_fire_at)
  if (waitingOnGate(task)) {
    const label = compact ? countdown : `等待时间窗 · ${countdown}`
    return { label, tone: 'warn', title: s.summary }
  }
  if (s.kind === 'cron') {
    const label = compact ? (countdown || '—') : `周期 · ${countdown || '—'}`
    return { label, tone: 'info', title: s.summary }
  }
  return { label: s.kind === 'at' ? '定时' : '时间窗', tone: 'info', title: s.summary }
}

export const NIGHT_WINDOW = { kind: 'window', window_start: '22:00', window_end: '06:00' }

export const CRON_PRESETS = [
  { value: '0 * * * *', label: '每小时' },
  { value: '0 9 * * *', label: '每天 09:00' },
  { value: '0 9 * * 1-5', label: '工作日 09:00' },
  { value: '0 9 * * 1', label: '每周一 09:00' },
]

export function browserTz() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || '' } catch { return '' }
}

/** The PUT/preview body for a draft. */
export function scheduleBody(draft) {
  const base = { kind: draft.kind, tz: draft.tz || undefined, enabled: draft.enabled }
  if (draft.kind === 'at') {
    // The input is the viewer's wall clock; sent as UTC so `tz` cannot
    // re-interpret it.
    const d = new Date(draft.run_at)
    return { ...base, run_at: Number.isNaN(d.valueOf()) ? draft.run_at : d.toISOString() }
  }
  if (draft.kind === 'window') {
    return { ...base, window_start: draft.window_start, window_end: draft.window_end }
  }
  return { ...base, cron_expr: draft.cron_expr }
}

/** A draft seeded from the saved schedule, or a nightly window by default. */
export function draftFrom(saved) {
  return {
    kind: saved?.kind || 'window',
    run_at: saved?.run_at ? toLocalInput(saved.run_at) : '',
    window_start: saved?.window_start || NIGHT_WINDOW.window_start,
    window_end: saved?.window_end || NIGHT_WINDOW.window_end,
    cron_expr: saved?.cron_expr || CRON_PRESETS[1].value,
    tz: saved?.tz || browserTz(),
    enabled: saved ? Boolean(saved.enabled) : true,
  }
}

/** "2026-10-24T01:00:00Z" → "2026-10-24T09:00" for a datetime-local input,
 *  in the viewer's zone (the server reads a zone-less run_at in `tz`). */
function toLocalInput(iso) {
  const d = parseUtc(iso)
  if (!d) return ''
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
