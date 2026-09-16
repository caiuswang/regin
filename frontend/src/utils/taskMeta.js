// The vocabulary the task surface renders: how a task is addressed, and what
// its priority and grouping axes are called.
//
// `taskRef` is the single place a task's display identifier is built. It is
// the integer primary key today because that is what `regin task add`, the
// notifications and every existing deep link already carry; routing every
// call site through here is what would make a project-prefixed key (ENG-42) a
// one-function change rather than a rewrite.

export function taskRef(task) {
  return task?.id == null ? '' : `#${task.id}`
}

export const PRIORITIES = [
  { value: 'urgent', label: 'Urgent', bars: 4, tone: 'danger' },
  { value: 'high', label: 'High', bars: 3, tone: 'warning' },
  { value: 'medium', label: 'Medium', bars: 2, tone: 'info' },
  { value: 'low', label: 'Low', bars: 1, tone: 'muted' },
  { value: 'none', label: 'No priority', bars: 0, tone: 'faint' },
]

const PRIORITY_BY_VALUE = Object.fromEntries(
  PRIORITIES.map(p => [p.value, p]),
)

export function priorityMeta(value) {
  return PRIORITY_BY_VALUE[value] || PRIORITY_BY_VALUE.none
}

export const STATUS_FILTERS = [
  { key: '', label: 'All' },
  { key: 'idea', label: 'Ideas' },
  { key: 'armed', label: 'Armed' },
  { key: 'queued', label: 'Queued' },
  { key: 'running', label: 'Running' },
  { key: 'done', label: 'Done' },
  { key: 'failed', label: 'Failed' },
  { key: 'archived', label: 'Archived' },
]

export const SORTS = [
  { value: 'created_desc', label: 'Newest' },
  { value: 'created_asc', label: 'Oldest' },
  { value: 'updated_desc', label: 'Recently updated' },
  { value: 'priority', label: 'Priority' },
]

export const GROUPS = [
  { value: 'none', label: 'No grouping' },
  { value: 'status', label: 'Status' },
  { value: 'priority', label: 'Priority' },
  { value: 'project', label: 'Project' },
]

const BACKLOG = { key: 'none', label: 'No project' }

// The archived count is honest — it is the true size of the archive — so the
// group has to be suppressed here rather than by reading 0. Archived is a
// separate world: it appears when someone asks for it, not because it is
// non-empty.
function statusGroups(counts, filters) {
  const wanted = filters.include_archived || filters.status === 'archived'
  return STATUS_FILTERS
    .filter(s => s.key && (wanted || s.key !== 'archived'))
    .map(s => ({ key: s.key, label: s.label, param: 'status',
                 count: counts?.by_status?.[s.key] || 0 }))
}

function priorityGroups(counts) {
  return PRIORITIES.map(p => ({ key: p.value, label: p.label,
                                param: 'priority',
                                count: counts?.by_priority?.[p.value] || 0 }))
}

function projectGroups(counts, projects) {
  const named = (projects || []).map(p => ({
    key: String(p.id), label: p.name, param: 'project_id',
    count: counts?.by_project?.[String(p.id)] || 0,
  }))
  return [...named, { ...BACKLOG, param: 'project_id',
                      count: counts?.by_project?.none || 0 }]
}

const BY_GROUP = {
  status: (counts, projects, filters) => statusGroups(counts, filters),
  priority: (counts) => priorityGroups(counts),
  project: (counts, projects) => projectGroups(counts, projects),
}

/**
 * The group headers to render, in display order, each with the server-side
 * count of what it would show and the query param that scopes its own feed.
 *
 * Built from `counts` rather than from the loaded rows: a header counted over
 * one 50-row page reports the size of the page, not of the group, and a
 * collapsed group would report zero.
 *
 * `filters` narrows the headers to the axis's own active filter. The counts
 * are deliberately blind to it — each header reports what it WOULD show — but
 * rendering them all while that filter is set puts two contradictory claims
 * about scope on one screen: a chip reading "Ideas" above a Done group full
 * of rows.
 */
export function groupsFor(groupBy, counts, projects, filters = {}) {
  const build = BY_GROUP[groupBy]
  if (!build) return []
  const groups = build(counts, projects, filters)
  if (!groups.length) return []
  const active = { status: filters.status, priority: filters.priority,
                   project_id: filters.project_id }[groups[0].param]
  return active ? groups.filter(g => g.key === active) : groups
}
