// Verdict → badge color for both grading axes. Single source so the
// list badges and the expanded report cards can never drift apart.
export const verdictColor = {
  satisfied: 'green', efficient: 'green',
  needs_revision: 'yellow', acceptable: 'yellow',
  fail: 'red', wasteful: 'red',
}

// A verdict's *tone* is the axis-agnostic outcome class. Both axes share the
// same three tones so distribution bars, accent rails, and worst-of rollups
// can reason about pass/warn/fail without caring which axis produced it.
// Anything unknown (a verdict we don't recognise, or a missing axis) is
// `unknown` — rendered neutral, never miscounted as a pass or a fail.
export const verdictTone = {
  satisfied: 'pass', efficient: 'pass',
  needs_revision: 'warn', acceptable: 'warn',
  fail: 'fail', wasteful: 'fail',
}

// Tone → Tailwind utility classes. Pill = the small uppercase chip; bar = the
// solid swatch used in distribution bars and accent rails; dot = a legend
// marker. Light-mode tuned (≥4.5:1 text on the soft fill).
export const TONE_META = {
  pass: { pill: 'bg-emerald-100 text-emerald-700', bar: 'bg-emerald-400', dot: 'bg-emerald-500', label: 'Pass' },
  warn: { pill: 'bg-amber-100 text-amber-800', bar: 'bg-amber-400', dot: 'bg-amber-500', label: 'Needs work' },
  fail: { pill: 'bg-red-100 text-red-700', bar: 'bg-red-400', dot: 'bg-red-500', label: 'Failed' },
  unknown: { pill: 'bg-slate-100 text-slate-500', bar: 'bg-slate-300', dot: 'bg-slate-400', label: 'Ungraded' },
}

// Worst-first ordering — drives a row's accent rail (its weakest axis sets the
// colour) and lets the list sort "problems first".
export const TONE_RANK = { fail: 0, warn: 1, pass: 2, unknown: 3 }

export function toneOf(verdict) {
  return verdictTone[verdict] || 'unknown'
}

export function toneMeta(verdict) {
  return TONE_META[toneOf(verdict)]
}

// The harshest tone across a set of verdicts (e.g. a row's two axes). Used for
// the accent rail and worst-first sorting. Returns 'unknown' for an empty set.
export function worstTone(verdicts) {
  let worst = 'unknown'
  for (const v of verdicts) {
    const t = toneOf(v)
    if (TONE_RANK[t] < TONE_RANK[worst]) worst = t
  }
  return worst
}
