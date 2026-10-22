// The R5 matrix: which verbs a defect offers, by its place on the two axes
// (`status` × triage). One table, so the row menu, the list page and the tests
// read the same answer.

export function defectState(d) {
  if (d.status === 'closed') return d.triage_state === 'declined' ? 'declined' : 'closed'
  if (d.feature_id) return 'attributed'
  return d.queue_position === 'snoozed' ? 'snoozed' : 'untriaged'
}

export const PRIMARY = {
  untriaged: 'attribute',
  snoozed: 'unsnooze',
  attributed: 'close',
  declined: 'restore',
  closed: 'reopen',
}

// Kept short on purpose: the symptom is edited by clicking it, and folding
// duplicates is one verb (Merge, from the duplicate's side), not two.
export const MENU = {
  untriaged: ['attribute', 'merge', 'decline', 'snooze', 'attach'],
  snoozed: ['unsnooze', 'attribute', 'merge', 'decline'],
  attributed: ['attribute', 'close', 'fix_create', 'fix_link', 'fix_clear',
               'merge', 'attach'],
  declined: ['restore'],
  closed: ['reopen'],
}

// The groups a separator is drawn between in the menu.
export const GROUP = {
  attribute: 0, unsnooze: 0, restore: 0, reopen: 0, close: 0,
  decline: 0, snooze: 0,
  fix_create: 1, fix_link: 1, fix_clear: 1,
  merge: 2,
  attach: 3,
}
