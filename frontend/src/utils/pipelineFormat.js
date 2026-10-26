// Shared readings for the Pipeline shell's Overview and Features tabs.

import { localDay, parseUtc } from './time.js'

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

//: The facts a loop stage prints, as `[part key, words, tone]`: only the
//: counts that move or ask for a decision. `ready` is the run-next count, not
//: a loop part.
const LOOP_FACTS = {
  Tasks: [['running', 'running'], ['ready', 'ready'], ['failed', 'failed', 'danger']],
  Features: [['building', 'building'], ['spec', 'in spec'], ['escaped', 'escaped', 'warn']],
  Defects: [['open', 'open'], ['triage', 'to triage', 'warn']],
}

/** One loop part's count, 0 when the stage or the part is absent. */
export function loopCount(loop, label, key) {
  const stage = (loop?.stages || []).find(s => s.label === label)
  return Number(stage?.parts?.find(p => p.key === key)?.n || 0)
}

// Every printed fact is a way to exactly its rows: the task groups by the
// next-step list's own group key, the feature stages by the stage filter.
// The triage queue is the Features tab's rail, which ignores the scope (D1).
function loopFactLink(label, key, scope) {
  if (label === 'Tasks') return { path: '/tasks', query: { ...scope, view: 'list', group: key } }
  if (label === 'Features') return { path: '/features', query: { ...scope, stage: key } }
  if (key === 'open') return { path: '/defects', query: { ...scope, status: 'open', queue: 'out' } }
  return { path: '/features', query: { ...scope } }
}

/** A loop stage's non-zero facts, each with the link to its rows. */
export function loopFacts(stage, ready, scope = {}) {
  const n = key => (key === 'ready' ? ready : stage.parts.find(p => p.key === key)?.n || 0)
  return (LOOP_FACTS[stage.label] || [])
    .map(([key, words, tone]) => ({ key, words, tone, n: n(key), to: loopFactLink(stage.label, key, scope) }))
    .filter(f => f.n > 0)
}

/** The loop's return edge as a sentence; '' when no fix task carries a defect. */
export function loopNote(fixTasks) {
  const n = fixTasks || 0
  if (!n) return ''
  return `${n} fix task${n === 1 ? '' : 's'} carr${n === 1 ? 'ies' : 'y'} open defects back into the queue`
}

export function loopBar(bar) {
  return STAGES.map(key => ({ key, label: key, n: Number(bar?.[key] || 0), tone: LOOP_FILL[key] }))
}

export function stageParts(bar) {
  return STAGES.map(key => ({ key, n: Number(bar?.[key] || 0), tone: STAGE_TONE[key] }))
}

/** "2h", "1d", "just now" — the Active column's compact age. */
export function ago(iso, now = Date.now()) {
  const at = parseUtc(iso)
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
  return localDay(iso)
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

//: A defect's symptom as a list row reads it: the first non-empty line, with
//: an embedded `![](image:N)` named rather than drawn.
export function symptomFirstLine(symptom) {
  const line = String(symptom || '').split('\n').find(l => l.trim()) || ''
  return line.trim().replace(/!\[[^\]]*\]\(image:(\d+)\)/g, '[image $1]')
}
