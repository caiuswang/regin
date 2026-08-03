// send_to_user message type → presentation. Single source for the filter
// chips, the list-row pill, and the detail-pane pill, so a type can never
// read one colour in the filter and another on the message it filters to.
// Severity-ascending — the store orders by the same scale.
//
// `tier` is how loudly a type may interrupt (see NOTIFICATION_TIERS below).
// It is a property of the *type*, not of any one surface, so the toast host,
// the OS bridge and the badge all agree on what a message is allowed to do.
export const INBOX_TYPES = [
  { type: 'progress', label: 'Progress', tier: 3, pill: 'inbox-pill-slate', dot: 'bg-slate-400', sel: 'bg-slate-100 border-slate-400 text-slate-700' },
  { type: 'note', label: 'Note', tier: 3, pill: 'inbox-pill-slate', dot: 'bg-slate-400', sel: 'bg-slate-100 border-slate-400 text-slate-700' },
  { type: 'lesson', label: 'Lesson', tier: 3, pill: 'inbox-pill-violet', dot: 'bg-violet-500', sel: 'bg-violet-50 border-violet-400 text-violet-700' },
  { type: 'result', label: 'Result', tier: 2, pill: 'inbox-pill-emerald', dot: 'bg-emerald-500', sel: 'bg-emerald-50 border-emerald-400 text-emerald-700' },
  { type: 'summary', label: 'Summary', tier: 2, pill: 'inbox-pill-indigo', dot: 'bg-indigo-500', sel: 'bg-indigo-50 border-indigo-400 text-indigo-700' },
  { type: 'warning', label: 'Warning', tier: 2, pill: 'inbox-pill-amber', dot: 'bg-amber-500', sel: 'bg-amber-50 border-amber-400 text-amber-800' },
  { type: 'blocker', label: 'Blocker', tier: 1, pill: 'inbox-pill-red', dot: 'bg-red-500', sel: 'bg-red-50 border-red-400 text-red-700' },
]

// How far a tier may go, loudest first. Tier 1 is the only one that survives
// inattention: it does not auto-dismiss, because the agent is stopped until
// it is dealt with.
export const NOTIFICATION_TIERS = {
  1: { key: 'interrupt', label: 'Interrupt', blurb: 'Banner until answered — the agent is paused.' },
  2: { key: 'toast', label: 'Toast', blurb: 'Toast, then folds into the badge.' },
  3: { key: 'count', label: 'Count only', blurb: 'No pop — only the unread badge moves.' },
}

const BY_TYPE = Object.fromEntries(INBOX_TYPES.map(t => [t.type, t]))

export function inboxTypeMeta(type) {
  return BY_TYPE[type] || BY_TYPE.progress
}

export function notificationTier(type) {
  return inboxTypeMeta(type).tier
}

// Interaction-required events reuse one per-session `msg_key` (see
// lib/agent_messages/event_notify.py), which is what marks a card as a
// decision the agent is parked on rather than a report.
export const DECISION_KEYS = new Set(['permission-pending', 'plan-pending'])

export function isDecisionMessage(message) {
  return DECISION_KEYS.has(message?.msg_key)
}
