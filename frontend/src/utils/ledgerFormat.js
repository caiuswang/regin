// The rendering rules `lib/features/ledger.py` established, ported so the two
// surfaces agree character-for-character on the things that carry meaning:
// `none declared` is not `$0.00`, a gap name keeps its hyphen, and a stage
// nobody fetched wears no chip at all.

import { localDay, parseUtc } from './time.js'

// TaskLinkCard.vue's map, shared rather than forked. It is deliberately NOT
// extended: `unknown` may never become a key here, because a missing stage
// that wears a chip reads as a stage somebody declared.
export const STAGE_COLOR = {
  spec: 'gray', building: 'blue', shipped: 'green', escaped: 'amber',
  // Taken down or dropped. Slate rather than red: it is a decision, not a
  // failure, and it costs no round.
  abandoned: 'slate',
}

export const GAP_BLURB = {
  no_usage_captured: 'sessions whose usage rows were never ingested — their cost is missing from the figure, not zero',
  model_unpriced: 'sessions on a model with no entry in the price table',
  zero_duration: 'sessions with no measurable active work',
  traceless_runs: 'pipeline runs with no trace to attribute',
}

export function money(value) {
  if (value === null || value === undefined) return 'none declared'
  return `$${Number(value).toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`
}

export function duration(ms) {
  if (!ms || ms <= 0) return '—'
  const total = Math.round(ms / 1000)
  const seconds = total % 60
  const minutes = Math.floor(total / 60) % 60
  const hours = Math.floor(total / 3600)
  return hours
    ? `${hours}h ${String(minutes).padStart(2, '0')}m`
    : `${minutes}m ${String(seconds).padStart(2, '0')}s`
}

export function plural(count, noun) {
  return count === 1 ? `${count} ${noun}` : `${count} ${noun}s`
}

export function percent(part, whole) {
  return whole ? `${((100 * part) / whole).toFixed(1)}%` : '—'
}

export function num(value) {
  return Number(value || 0).toLocaleString('en-US')
}

export function gapList(gaps) {
  return Object.entries(gaps || {})
    .filter(([, count]) => count)
    .map(([name, count]) => ({
      name, count,
      label: name.replace(/_/g, '-'),
      blurb: GAP_BLURB[name] || name,
    }))
}

export function gapTotal(gaps) {
  return gapList(gaps).reduce((n, g) => n + g.count, 0)
}

export function shortDate(iso) {
  return localDay(iso)
}

// "as of 14 Sep, 03:12" — a stage is a dated local fact, never a live read.
export function asOf(iso) {
  if (!iso) return null
  const parsed = parseUtc(iso)
  if (!parsed) return iso
  return parsed.toLocaleString('en-GB', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

// The ledger's session rows, keyed by the task whose run produced them. Read
// off the same rows the Sessions card draws, so a task's figure and the
// evidence list can never disagree about which sessions exist.
export function sessionsByTask(rows) {
  const out = new Map()
  for (const r of rows || []) {
    if (r.task_id === null || r.task_id === undefined) continue
    if (!out.has(r.task_id)) out.set(r.task_id, [])
    out.get(r.task_id).push(r)
  }
  return out
}

// A sum over rows whose cost may be missing. The qualifier rides inside the
// number: `≥ $4.10` when some rows have no usage, `no usage` when none do —
// never `$0.00`, which would be a claim, not an absence. A shared session's
// cost is counted under its primary feature, so it adds nothing here.
export function costSum(rows) {
  const own = (rows || []).filter(r => r.is_primary !== false)
  if (!own.length) return rows?.length ? 'charged elsewhere' : 'no usage'
  const known = own.filter(r => r.cost_usd !== null && r.cost_usd !== undefined)
  if (!known.length) return 'no usage'
  const total = money(known.reduce((n, r) => n + r.cost_usd, 0))
  return known.length < own.length ? `≥ ${total}` : total
}
