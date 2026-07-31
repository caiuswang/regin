// send_to_user message type → presentation. Single source for the filter
// chips, the list-row pill, and the detail-pane pill, so a type can never
// read one colour in the filter and another on the message it filters to.
// Severity-ascending — the store orders by the same scale.
export const INBOX_TYPES = [
  { type: 'progress', label: 'Progress', pill: 'inbox-pill-slate', dot: 'bg-slate-400', sel: 'bg-slate-100 border-slate-400 text-slate-700' },
  { type: 'note', label: 'Note', pill: 'inbox-pill-slate', dot: 'bg-slate-400', sel: 'bg-slate-100 border-slate-400 text-slate-700' },
  { type: 'lesson', label: 'Lesson', pill: 'inbox-pill-violet', dot: 'bg-violet-500', sel: 'bg-violet-50 border-violet-400 text-violet-700' },
  { type: 'result', label: 'Result', pill: 'inbox-pill-emerald', dot: 'bg-emerald-500', sel: 'bg-emerald-50 border-emerald-400 text-emerald-700' },
  { type: 'summary', label: 'Summary', pill: 'inbox-pill-indigo', dot: 'bg-indigo-500', sel: 'bg-indigo-50 border-indigo-400 text-indigo-700' },
  { type: 'warning', label: 'Warning', pill: 'inbox-pill-amber', dot: 'bg-amber-500', sel: 'bg-amber-50 border-amber-400 text-amber-800' },
  { type: 'blocker', label: 'Blocker', pill: 'inbox-pill-red', dot: 'bg-red-500', sel: 'bg-red-50 border-red-400 text-red-700' },
]

const BY_TYPE = Object.fromEntries(INBOX_TYPES.map(t => [t.type, t]))

export function inboxTypeMeta(type) {
  return BY_TYPE[type] || BY_TYPE.progress
}

// Interaction-required events reuse one per-session `msg_key` (see
// lib/agent_messages/event_notify.py), which is what marks a card as a
// decision the agent is parked on rather than a report.
export const DECISION_KEYS = new Set(['permission-pending', 'plan-pending'])

export function isDecisionMessage(message) {
  return DECISION_KEYS.has(message?.msg_key)
}
