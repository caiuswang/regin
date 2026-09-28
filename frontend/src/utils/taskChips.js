// The Tasks toolbar's chips: every filter off its default, as a removable
// "<field> <value> ×", and what removing it writes back into the URL.

import { STATUS_FILTERS, priorityChipLabel } from './taskMeta.js'

function statusLabel(key) {
  return STATUS_FILTERS.find(s => s.key === key)?.label || key
}

function userLabel(users, id) {
  return users.find(u => String(u.id) === String(id))?.name || `#${id}`
}

const TEXT_CHIPS = [
  { key: 'tag', field: 'Tag' },
  { key: 'source', field: 'Source' },
]

/**
 * `mode` is the Filter menu's: 'grouped' (next step, board), 'flat', or
 * 'graph' (grouped's axes plus "Open chains only"). A chip is only shown for an axis that reading honours — a status
 * chip over the board would name a filter the columns ignore.
 */
export function taskChips(state, { mode = 'grouped', mobile = false, users = [] } = {}) {
  const chips = []
  if (mode === 'graph' && state.open !== '0') {
    chips.push({ key: 'open', field: 'Show', value: 'Open chains only', patch: { open: '0' } })
  }
  if (state.priority) {
    chips.push({ key: 'priority', field: mobile ? '' : 'Priority',
                 value: priorityChipLabel(state.priority, { compact: mobile }),
                 patch: { priority: '' } })
  }
  if (mode === 'flat' && state.status) {
    chips.push({ key: 'status', field: 'Status', value: statusLabel(state.status),
                 patch: { status: '' } })
  }
  for (const { key, field } of TEXT_CHIPS) {
    if (state[key]) chips.push({ key, field, value: state[key], patch: { [key]: '' } })
  }
  if (state.feature_id) {
    chips.push({ key: 'feature', field: 'Feature', value: `#${state.feature_id}`,
                 patch: { feature_id: '' } })
  }
  if (state.assignee) {
    chips.push({ key: 'assignee', field: 'Assignee', value: userLabel(users, state.assignee),
                 patch: { assignee: '' } })
  }
  if (state.unfeatured === '1') {
    chips.push({ key: 'unfeatured', field: 'Show', value: 'No feature only',
                 patch: { unfeatured: '' } })
  }
  if (mode === 'flat' && state.archived === '1') {
    chips.push({ key: 'archived', field: 'Show', value: 'Archived', patch: { archived: '' } })
  }
  return chips
}
