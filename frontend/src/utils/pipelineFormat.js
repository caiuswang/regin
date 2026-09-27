// Shared readings for the Pipeline shell's Overview and Features tabs.

//: The four live stages in track order, and the tone each one wears in a bar,
//: a legend dot and a stage label. Abandoned is never drawn.
export const STAGES = ['spec', 'building', 'shipped', 'escaped']

export const STAGE_TONE = {
  spec: 'idle', building: 'info', shipped: 'success', escaped: 'warning',
}

//: Loop part key → tone. The keys are `lib/pipeline/overview.py`'s.
export const PART_TONE = {
  idea: 'idle', armed: 'info', queued: 'orange', running: 'agent',
  done: 'success', failed: 'danger',
  spec: 'idle', building: 'info', shipped: 'success', escaped: 'warning',
  triage: 'orange', open: 'danger', closed: 'success',
}

export function stageParts(bar) {
  return STAGES.map(key => ({ key, n: Number(bar?.[key] || 0), tone: STAGE_TONE[key] }))
}

function parse(iso) {
  if (!iso) return null
  const text = String(iso)
  const hasZone = /[zZ]|[+-]\d\d:?\d\d$/.test(text)
  const parsed = new Date(hasZone ? text : `${text.replace(' ', 'T')}Z`)
  return Number.isNaN(parsed.valueOf()) ? null : parsed
}

/** "2h", "1d", "just now" — the Active column's compact age. */
export function ago(iso, now = Date.now()) {
  const at = parse(iso)
  if (!at) return '—'
  const min = Math.floor((now - at.getTime()) / 60000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min}m`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h`
  const day = Math.floor(hr / 24)
  if (day < 60) return `${day}d`
  return `${Math.floor(day / 30)}mo`
}

/** "Sep 25" — a defect's opened date on a triage card. */
export function monthDay(iso) {
  const at = parse(iso)
  if (!at) return '—'
  return at.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

/** "D-94" — the defect id as the redesign prints it. */
export function defectRef(id) {
  return `D-${id}`
}

/** A run-next row's second line, composed from the fields the API names. */
export function runNextSub(row) {
  const parts = []
  if (row.fixes_defects?.length) {
    parts.push(`Fix for ${row.fixes_defects.map(defectRef).join(', ')}`)
  }
  if (row.feature_id) {
    const step = row.step ? ` · step ${row.step.n} of ${row.step.of}` : ''
    parts.push(`#${row.feature_id} ${row.feature_title || ''}`.trim() + step)
  }
  return parts.length ? parts.join(' · ') : 'No feature'
}

/** A running row's second line: the run-next line, plus the attempt once it is a retry. */
export function runningSub(row) {
  const attempt = row.live?.attempt > 1 ? ` · attempt ${row.live.attempt}` : ''
  return `${runNextSub(row)}${attempt}`
}

/** "14 turns · $0.82" — what the running session has spent so far; blank parts drop out. */
export function runningMeta(row) {
  const parts = []
  if (row.turns != null) parts.push(`${row.turns} ${row.turns === 1 ? 'turn' : 'turns'}`)
  if (row.cost_usd != null) parts.push(`$${Number(row.cost_usd).toFixed(2)}`)
  return parts.join(' · ')
}

/** "1 escaped · active 2h ago" — a project row's status line. */
export function projectStatusLine(row) {
  const active = row.last_activity ? ago(row.last_activity) : null
  const when = active && active !== '—'
    ? (active === 'just now' ? 'active just now' : `active ${active} ago`)
    : null
  const lead = row.escaped ? `${row.escaped} escaped` : null
  const line = [lead, when].filter(Boolean).join(' · ')
  if (!line) return 'No activity yet'
  return line.charAt(0).toUpperCase() + line.slice(1)
}
