// The derived project status, as `lib/pipeline/projects.py::status_of` decides
// it. Labels only — the decision lives server-side, once.
export const STATUS_LABEL = {
  // Linear's word for a project with nothing in flight. Not "No features":
  // a project whose features were all abandoned still lists them.
  empty: 'Backlog',
  planned: 'Planned',
  in_progress: 'In progress',
  completed: 'Completed',
}

export const STATUS_COLOR = {
  empty: 'gray', planned: 'purple', in_progress: 'blue', completed: 'green',
}

// Left to right, the order a feature walks. Abandoned is off the bar: it is
// not part of `live`, so drawing it would make the segments outgrow the total.
export const BAR_STAGES = ['shipped', 'escaped', 'building', 'spec']

export function projectName(p) {
  if (!p) return ''
  if (p.scope === 'none') return 'No project'
  return p.name || `project #${p.id}`
}

export function percent(progress) {
  if (!progress?.live) return null
  return Math.round((100 * progress.done) / progress.live)
}

// Tasks still to do: everything not `done` or `archived`. A `failed` task is
// open — it has not delivered, and re-arming it is the next move.
const SETTLED = new Set(['done', 'archived'])
export function openTasks(tasks) {
  return Object.entries(tasks?.by_status || {})
    .filter(([status]) => !SETTLED.has(status))
    .reduce((n, [, count]) => n + count, 0)
}

export function scopedLink(path, scope) {
  return { path, query: { project: scope } }
}

export const OPEN_TASKS_HINT = 'every top-level task not done or archived — a failed task counts as open; a feature\'s steps are counted on the feature'
