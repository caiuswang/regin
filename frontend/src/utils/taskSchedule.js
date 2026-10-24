// What a task's schedule says on a row, a card or a lane node — and the
// request bodies the detail card and the bulk bar send. One home, so every
// surface reads "waiting for its window" off the same rule.
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
 *  the prefix is what has to give, never the "how long until". */
export function scheduleChip(task, compact = false) {
  const s = task?.schedule
  if (!s) return null
  if (!s.enabled) return { label: 'Schedule off', tone: 'muted', title: s.summary }
  const countdown = untilLabel(s.next_fire_at)
  if (waitingOnGate(task)) {
    const label = compact ? countdown : `Starts ${countdown}`
    return { label, tone: 'warn', title: s.summary }
  }
  if (s.kind === 'cron') {
    const label = compact ? (countdown || '—') : `Repeats · ${countdown || '—'}`
    return { label, tone: 'info', title: s.summary }
  }
  return { label: s.kind === 'at' ? 'Scheduled' : 'Time window', tone: 'info', title: s.summary }
}

export const NIGHT_WINDOW = { kind: 'window', window_start: '22:00', window_end: '06:00' }

export const MODES = [
  { value: 'once', label: 'Once' },
  { value: 'repeat', label: 'Repeat' },
  { value: 'window', label: 'Time window' },
]

export const FREQUENCIES = [
  { value: 'hourly', label: 'Every hour' },
  { value: 'daily', label: 'Every day' },
  { value: 'weekdays', label: 'Every weekday (Mon–Fri)' },
  { value: 'weekly', label: 'Every week' },
  { value: 'custom', label: 'Custom (cron)' },
]

// Cron day-of-week numbers, in the order a week reads.
export const WEEKDAYS = [
  { value: 1, label: 'Mon' }, { value: 2, label: 'Tue' }, { value: 3, label: 'Wed' },
  { value: 4, label: 'Thu' }, { value: 5, label: 'Fri' }, { value: 6, label: 'Sat' },
  { value: 0, label: 'Sun' },
]

export function browserTz() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || '' } catch { return '' }
}

export function timeZones(current) {
  let zones = []
  try { zones = Intl.supportedValuesOf('timeZone') } catch { zones = [] }
  if (!zones.includes('UTC')) zones = ['UTC', ...zones]
  if (current && !zones.includes(current)) zones = [current, ...zones]
  return zones
}

const pad = n => String(n).padStart(2, '0')
const hhmm = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`
const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

/** The cron expression a Repeat draft stands for. */
export function cronFromDraft(draft) {
  if (draft.freq === 'custom') return draft.cron_expr
  const [h, m] = (draft.repeat_time || '09:00').split(':').map(Number)
  if (draft.freq === 'hourly') return `${Number(draft.minute) || 0} * * * *`
  if (draft.freq === 'daily') return `${m} ${h} * * *`
  if (draft.freq === 'weekdays') return `${m} ${h} * * 1-5`
  const days = [...(draft.days || [])].sort((a, b) => a - b)
  return `${m} ${h} * * ${days.length ? days.join(',') : '*'}`
}

/** A saved cron expression read back into the builder; anything the builder
 *  cannot express stays as Custom so it is never silently rewritten. */
export function repeatFromCron(expr) {
  const parts = String(expr || '').trim().split(/\s+/)
  const custom = { freq: 'custom', cron_expr: expr }
  if (parts.length !== 5) return custom
  const [min, hour, dom, mon, dow] = parts
  const num = v => /^\d+$/.test(v)
  if (dom !== '*' || mon !== '*' || !num(min)) return custom
  if (hour === '*' && dow === '*') return { freq: 'hourly', minute: Number(min) }
  if (!num(hour)) return custom
  const repeat_time = `${pad(hour)}:${pad(min)}`
  if (dow === '*') return { freq: 'daily', repeat_time }
  if (dow === '1-5') return { freq: 'weekdays', repeat_time }
  if (/^\d(,\d)*$/.test(dow)) {
    return { freq: 'weekly', repeat_time, days: dow.split(',').map(d => Number(d) % 7) }
  }
  return custom
}

/** The PUT/preview body for a draft. */
export function scheduleBody(draft) {
  const base = { tz: draft.tz || undefined, enabled: draft.enabled }
  if (draft.mode === 'once') {
    // The inputs are the viewer's wall clock; sent as UTC so `tz` cannot
    // re-interpret them.
    const d = new Date(`${draft.date}T${draft.time}`)
    return { ...base, kind: 'at', run_at: Number.isNaN(d.valueOf()) ? '' : d.toISOString() }
  }
  if (draft.mode === 'window') {
    return { ...base, kind: 'window', window_start: draft.window_start, window_end: draft.window_end }
  }
  return { ...base, kind: 'cron', cron_expr: cronFromDraft(draft) }
}

/** Quick picks for a one-off start, as { date, time } in the viewer's zone. */
export function oncePresets(now = new Date()) {
  const inHour = new Date(now.valueOf() + 60 * 60000)
  inHour.setMinutes(Math.ceil(inHour.getMinutes() / 5) * 5, 0, 0)
  const tonight = new Date(now)
  tonight.setHours(22, 0, 0, 0)
  if (tonight <= now) tonight.setDate(tonight.getDate() + 1)
  const tomorrow = new Date(now)
  tomorrow.setDate(tomorrow.getDate() + 1)
  tomorrow.setHours(9, 0, 0, 0)
  return [
    { label: 'In 1 hour', at: inHour },
    { label: tonight.getDate() === now.getDate() ? 'Tonight 22:00' : 'Tomorrow 22:00', at: tonight },
    { label: 'Tomorrow 09:00', at: tomorrow },
  ].map(p => ({ label: p.label, date: ymd(p.at), time: hhmm(p.at) }))
}

/** A draft seeded from the saved schedule, or a one-off start an hour out. */
export function draftFrom(saved, now = new Date()) {
  const soon = oncePresets(now)[0]
  // A one-off that already fired seeds a fresh time, not a past one the
  // server would refuse.
  const past = saved?.run_at ? parseUtc(saved.run_at) : null
  const runAt = past && past > now ? past : null
  const mode = { at: 'once', window: 'window', cron: 'repeat' }[saved?.kind] || 'once'
  return {
    mode,
    date: runAt ? ymd(runAt) : soon.date,
    time: runAt ? hhmm(runAt) : soon.time,
    freq: 'daily',
    repeat_time: '09:00',
    minute: 0,
    days: [1],
    cron_expr: '0 9 * * *',
    ...(saved?.cron_expr ? repeatFromCron(saved.cron_expr) : {}),
    window_start: saved?.window_start || NIGHT_WINDOW.window_start,
    window_end: saved?.window_end || NIGHT_WINDOW.window_end,
    tz: saved?.tz || browserTz(),
    enabled: saved ? Boolean(saved.enabled) : true,
  }
}

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

/** One plain sentence for a saved schedule: "Runs once on Oct 24, 09:00". */
export function describe(saved, stamp) {
  if (!saved) return ''
  if (saved.kind === 'at') return `Runs once at ${stamp(saved.run_at)}`
  if (saved.kind === 'window') {
    return `Starts only between ${saved.window_start} and ${saved.window_end} (${saved.tz})`
  }
  const r = repeatFromCron(saved.cron_expr)
  const tz = ` (${saved.tz})`
  if (r.freq === 'hourly') return `Repeats every hour at :${pad(r.minute)}`
  if (r.freq === 'daily') return `Repeats every day at ${r.repeat_time}${tz}`
  if (r.freq === 'weekdays') return `Repeats every weekday at ${r.repeat_time}${tz}`
  if (r.freq === 'weekly') {
    return `Repeats every ${r.days.map(d => DAY_NAMES[d]).join(', ')} at ${r.repeat_time}${tz}`
  }
  return `Repeats on cron ${saved.cron_expr}${tz}`
}
