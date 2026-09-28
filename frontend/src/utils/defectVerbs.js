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

// Edit and Absorb stay on a closed defect: its wording can be corrected after
// the fact, and a later duplicate can still be folded into it.
export const MENU = {
  untriaged: ['attribute', 'merge', 'decline', 'snooze', 'edit', 'paths', 'attach'],
  snoozed: ['unsnooze', 'attribute', 'merge', 'decline', 'edit', 'paths'],
  attributed: ['attribute', 'close', 'fix_create', 'fix_link', 'fix_clear',
               'merge', 'absorb', 'edit', 'paths', 'attach'],
  declined: ['restore', 'edit'],
  closed: ['reopen', 'edit', 'paths', 'absorb'],
}

// The groups a separator is drawn between in the menu.
export const GROUP = {
  attribute: 0, unsnooze: 0, restore: 0, reopen: 0, close: 0,
  decline: 0, snooze: 0,
  fix_create: 1, fix_link: 1, fix_clear: 1,
  merge: 2, absorb: 2,
  edit: 3, paths: 3, attach: 3,
}
