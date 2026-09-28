// Shared readings for the Pipeline shell's Overview and Features tabs.

//: The four live stages in track order, and the tone each one wears in a bar,
//: a legend dot and a stage label. Abandoned is never drawn.
export const STAGES = ['spec', 'building', 'shipped', 'escaped']

export const STAGE_TONE = {
  spec: 'idle', building: 'info', shipped: 'success', escaped: 'warning',
}

//: The Overview's bar fills (spec · Loop facts): neutral by how far a part has
//: got, and coloured only where a human must act or something failed. Keys are
//: `lib/pipeline/overview.py`'s loop parts and a stage bar's stages.
export const LOOP_FILL = {
  idea: 'unstarted', spec: 'unstarted',
  armed: 'flight', queued: 'flight', running: 'flight', building: 'flight', open: 'flight',
  done: 'settled', shipped: 'settled', closed: 'settled',
  escaped: 'human', triage: 'human',
  failed: 'danger',
}

export function loopParts(parts) {
  return (parts || []).map(p => ({ ...p, tone: LOOP_FILL[p.key] || 'unstarted' }))
}

export function loopBar(bar) {
  return STAGES.map(key => ({ key, label: key, n: Number(bar?.[key] || 0), tone: LOOP_FILL[key] }))
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

/** "active 2h ago" — a project row's activity line. */
export function projectActivity(row) {
  const active = row.last_activity ? ago(row.last_activity) : null
  if (!active || active === '—') return 'no activity yet'
  return active === 'just now' ? 'active just now' : `active ${active} ago`
}

/** "3 escaped · 4 open defects" — what asks for a look; '' when nothing does. */
export function projectAttention(row) {
  return [
    row.escaped ? `${row.escaped} escaped` : '',
    row.open_defects ? `${row.open_defects} open defect${row.open_defects === 1 ? '' : 's'}` : '',
  ].filter(Boolean).join(' · ')
}

//: A feature's defects as one phrase. `open / total` read as a progress
//: fraction — "0 / 3" looked like nothing fixed when all three were closed.
export function defectTally(defects) {
  const total = Number(defects?.total || 0)
  const open = Number(defects?.open || 0)
  if (!total) return 'none'
  return open ? `${open} open of ${total}` : `all ${total} closed`
}

//: `Defect.round` counts reopenings of ONE defect; it is not the feature's
//: round, and "round 1" on every card read as though it were. Blank until
//: the defect has actually been reopened.
export function reopenedLabel(round) {
  const reopens = Number(round || 1) - 1
  return reopens > 0 ? `reopened ${reopens}×` : ''
}
