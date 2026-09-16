// The ONE session-status vocabulary. Every surface that puts a word on a
// session — the sessions list and cards, the trace header, the ⌘J palette and
// its rail, the /live picker and NOW zone — resolves its label, dot, glyph and
// description here.
//
// It exists because it did not: the list called a silent session `idle`, the
// palette called the same session `quiet`, the picker `idle`, the NOW zone
// `inactive`, and the server `inactive-stale`. Five words, one meaning, and a
// reader who opened the palette from the list saw the status change under
// them. So the words are keyed on MEANING, not on surface, and the two ways
// regin decides what a session is doing both funnel into these six keys.
//
// The words track the server's own phase vocabulary (`_session_phase` in
// web/blueprints/trace/sessions.py) wherever the server has a word, so UI copy
// and server verdict cannot drift apart. In particular `idle` is NOT a label:
// the server spends it on "alive, between spans" (which reads as `working`
// here), and reusing it for "gone silent" is exactly the collision this
// module removes.
//
// Colour is an ATTENTION ramp, not a severity ramp. The question a row answers
// is "does this want something from me", not "how bad is this", so the hues
// descend by claim on the reader — amber (acts on you) → emerald (moving
// alone) → blue (finished) → slate (nothing to do). Two consequences worth
// keeping:
//
//   Danger red is reserved for a genuine FAILURE and nothing else. Silence is
//   not a fault: a session goes quiet 10 minutes after you walk away from it
//   (INACTIVE_THRESHOLD_SEC), so painting that red made most of a 12-row list
//   shout. No phase the server emits today is a failure, so no state is red.
//
//   Exactly one state may demand action. If dormancy and `awaiting` both
//   shout, neither does — amber only works because it is scarce. That
//   scarcity argument is about LISTS; the single-session /live header still
//   paints `inactive-stale` amber (style.css `.live-status-stale`), where
//   there is no competing row for the colour to lose against.
//
// Shape carries the state independently of hue; the glyphs are the layer that
// survives colour-blindness and an 8px rail dot.

export const STATES = {
  working: {
    label: 'working',
    dot: 'var(--color-success)',
    soft: 'var(--color-success-soft)',
    strong: 'var(--color-success-strong)',
    glyph: '●',
    pulse: true,
    desc: 'Spans are still arriving.',
  },
  awaiting: {
    label: 'needs you',
    dot: 'var(--color-warning)',
    soft: 'var(--color-warning-soft)',
    strong: 'var(--color-warning-strong)',
    glyph: '◆',
    pulse: true,
    desc: 'Stopped on a question or an approval.',
  },
  // Alive, but not mid-turn — the agent is sitting at the prompt waiting for
  // you. Only a run regin OWNS can report this: the span heuristic cannot tell
  // "parked at the prompt" from "abandoned nine hours ago", which is why every
  // other session's silence reads `inactive` instead. Steady green, no pulse:
  // same liveness as `working`, none of its motion.
  idle: {
    label: 'idle',
    dot: 'var(--color-success)',
    soft: 'var(--color-success-soft)',
    strong: 'var(--color-success-strong)',
    glyph: '◐',
    pulse: false,
    desc: 'Alive and waiting for your prompt.',
  },
  // Fainter than `starting`: both are inert, but one is about to produce spans
  // and this one has stopped producing them, so it recedes further. The order
  // holds under inversion — slate-400 is the dimmer of the two in both themes.
  inactive: {
    label: 'inactive',
    dot: 'var(--color-fg-faint)',
    soft: 'var(--color-surface-2)',
    strong: 'var(--color-fg-muted)',
    glyph: '■',
    pulse: false,
    desc: 'Silent past the stale window. Nothing is wrong; it just stopped.',
  },
  ended: {
    label: 'ended',
    dot: 'var(--color-info)',
    soft: 'var(--color-info-soft)',
    strong: 'var(--color-info-strong)',
    glyph: '✓',
    pulse: false,
    desc: 'Finished. Stays here for the day, then falls back to history.',
  },
  // A sub-kind of `ended`, kept apart only on the surfaces that can tell them
  // apart (`ended_reason` rides the sessions list, not the switcher roster).
  // It reads the same as `ended` because it IS ended — an operator's decision
  // rather than a lifecycle event, which the description is what carries.
  closed: {
    label: 'closed',
    dot: 'var(--color-info)',
    soft: 'var(--color-info-soft)',
    strong: 'var(--color-info-strong)',
    glyph: '✓',
    pulse: false,
    desc: 'Closed by hand. Its trace data is kept.',
  },
  starting: {
    label: 'starting',
    dot: 'var(--color-fg-subtle)',
    soft: 'var(--color-surface-2)',
    strong: 'var(--color-fg-muted)',
    glyph: '○',
    pulse: false,
    desc: 'A run regin launched, not yet producing spans.',
  },
}

// Every value `_PHASE_PRECEDENCE` can emit. `stopping` is working, not
// starting: the session is still busy with something you cannot type into,
// which is what the working dot means. Exhaustive on purpose — see
// `statusFromPhase`.
const PHASE_STATUS = {
  working: 'working',
  idle: 'idle',
  stopping: 'working',
  starting: 'starting',
  'waiting-permission': 'awaiting',
  'waiting-input': 'awaiting',
  'inactive-stale': 'inactive',
  ended: 'ended',
}

// The client-side recency verdict (`sessionLiveness` in sessionActivity.js),
// which is what the surfaces without a runner phase have. Its `idle` is the
// OPPOSITE of the server's: it means "nothing ingested for over 10 minutes",
// i.e. this vocabulary's `inactive`. And its `active` can only ever be
// `working`: recency proves spans arrived recently, never that the agent is
// sitting at the prompt — only a runner knows that.
const LIVENESS_STATUS = {
  active: 'working',
  idle: 'inactive',
  ended: 'ended',
  closed: 'closed',
}

// An unmapped phase must not render as a blank dot. `inactive` is the honest
// fallback: we do not know that it is moving, and we do not know that it is
// finished — and not-knowing is a neutral, not a warning.
export function statusFromPhase(phase) {
  return PHASE_STATUS[phase] || 'inactive'
}

export function statusFromLiveness(liveness) {
  return LIVENESS_STATUS[liveness] || 'inactive'
}

export function statusMeta(status) {
  return STATES[status] || STATES.inactive
}

export function statusLabel(status) {
  return statusMeta(status).label
}

// The FINE-GRAINED vocabulary: one word per server phase, for the two surfaces
// that watch a single session closely enough to want the distinctions the
// six-state fold above drops — `idle` (alive at the prompt) apart from
// `working`, and `stopping` apart from both. It is the same set of words, not
// a synonym list: every entry either appears in STATES or names a phase STATES
// deliberately folds away.
export const PHASE_LABEL = {
  working: 'working',
  idle: 'idle',
  starting: 'starting',
  stopping: 'stopping',
  'waiting-permission': 'needs you',
  'waiting-input': 'needs you',
  'inactive-stale': 'inactive',
  ended: 'ended',
}

export function phaseLabel(phase) {
  return PHASE_LABEL[phase] || PHASE_LABEL['inactive-stale']
}

// The client-side recency verdict expressed as a server phase, so a surface
// with no phase in its payload can still speak the phase vocabulary. `active`
// becomes `idle`, not `working`: recency proves the session is ALIVE, never
// that it is mid-turn.
const LIVENESS_PHASE = {
  active: 'idle',
  idle: 'inactive-stale',
  ended: 'ended',
  closed: 'ended',
}

export function phaseFromLiveness(liveness) {
  return LIVENESS_PHASE[liveness] || 'inactive-stale'
}

// The status of a LIST row: the runner's verdict when regin owns the run,
// else the recency heuristic. `runner_phase` is served only for a session
// regin launched (`agent_sdk.run_phase`), so its presence is itself the
// signal — a session regin merely traces has no runner to ask and falls back.
//
// Replace, never merge. A runner that says `idle` is not a weaker `inactive`
// to be reconciled against span ages; it is the authority, and the heuristic
// it displaces exists only because most sessions have no runner at all.
export function statusOfRow(row, liveness) {
  // An ended session outranks the runner, exactly as the server's own rollup
  // does (`_session_phase` returns 'ended' before consulting the override).
  // `_aliases` is only pruned on run teardown, so a child `/clear` retired
  // still resolves to the live run and `run_phase` answers for it — without
  // this guard that retired row renders green.
  if (liveness === 'ended' || liveness === 'closed') {
    return statusFromLiveness(liveness)
  }
  return row?.runner_phase
    ? statusFromPhase(row.runner_phase)
    : statusFromLiveness(liveness)
}

// "Active now" for a LIST row, resolved the same way its chip is — so the
// group header, the facet count and the badge cannot disagree with the word
// beside them. Mirrors `_session_active_clause` in
// web/blueprints/trace/sessions.py, which the `active=` filter and the
// `active_count` facet share; the two must move together.
const ACTIVE_STATUSES = new Set(['working', 'idle', 'awaiting'])

export function isActiveRowStatus(row, liveness) {
  return ACTIVE_STATUSES.has(statusOfRow(row, liveness))
}
