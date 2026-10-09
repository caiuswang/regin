/**
 * The dependency editor's two derived views of one `/api/tasks/graph` read:
 * the candidate list grouped by feature, and the one-hop preview subset.
 */

export const PREVIEW_CONTEXT_CAP = 50

// A done or archived task can never hold this one back, so it is not offered
// — unless it is already an edge, which the reader must still be able to drop.
const SETTLED = new Set(['done', 'archived'])

export function candidatesFor(nodes, taskId, pickedIds) {
  return nodes.filter(n => n.id !== taskId
    && (pickedIds.has(n.id) || !SETTLED.has(n.status)))
}

function matches(node, needle) {
  return String(node.id).includes(needle)
    || String(node.title || '').toLowerCase().includes(needle)
}

function sortGroup(rows, pickedIds) {
  return [...rows].sort((a, b) =>
    (pickedIds.has(b.id) - pickedIds.has(a.id)) || (a.id - b.id))
}

/**
 * `[{ key, label, rows }]` — the current feature first, then every other
 * feature, then loose tasks. Picked rows always pass the filter, so a filter
 * can never hide an edge the save is about to keep.
 */
export function groupCandidates(candidates, { featureId, featureLabel, pickedIds, filter }) {
  const needle = (filter || '').trim().toLowerCase()
  const shown = needle
    ? candidates.filter(n => pickedIds.has(n.id) || matches(n, needle))
    : candidates
  const own = []
  const other = []
  const loose = []
  for (const node of shown) {
    if (node.feature_id == null) loose.push(node)
    else if (featureId != null && node.feature_id === featureId) own.push(node)
    else other.push(node)
  }
  return [
    { key: 'own', label: featureLabel, rows: own },
    { key: 'other', label: featureId == null ? 'Features' : 'Other features', rows: other },
    { key: 'loose', label: 'Loose tasks', rows: loose },
  ]
    .filter(g => g.rows.length)
    .map(g => ({ ...g, rows: sortGroup(g.rows, pickedIds) }))
}

/**
 * The preview: the task, each picked dependency (an edge into the task), each
 * direct dependent (an edge out of it), and — so there is something to click
 * on — the unpicked open tasks of the task's own feature. Context beyond the
 * cap is counted, not drawn.
 */
export function previewSubset({ nodes, edges, taskId, picked, featureId, cap = PREVIEW_CONTEXT_CAP }) {
  const byId = new Map(nodes.map(n => [n.id, n]))
  const self = byId.get(taskId)
  if (!self) return { nodes: [], edges: [], roles: new Map(), hidden: 0 }
  const roles = new Map([[taskId, 'current']])
  const outEdges = []
  for (const edge of picked) {
    if (!byId.has(edge.id) || edge.id === taskId) continue
    roles.set(edge.id, 'picked')
    outEdges.push({ task_id: taskId, depends_on_id: edge.id, kind: edge.kind })
  }
  const dependents = edges.filter(e => e.depends_on_id === taskId && byId.has(e.task_id)
    && !roles.has(e.task_id))
  const shownDeps = dependents.slice(0, cap)
  for (const e of shownDeps) {
    roles.set(e.task_id, 'dependent')
    outEdges.push({ task_id: e.task_id, depends_on_id: taskId, kind: e.kind || 'requires' })
  }
  const room = cap - shownDeps.length
  if (featureId != null && room > 0) {
    nodes
      .filter(n => n.feature_id === featureId && !roles.has(n.id) && !SETTLED.has(n.status))
      .slice(0, room)
      .forEach(n => roles.set(n.id, 'ghost'))
  }
  return {
    nodes: [...roles.keys()].map(id => byId.get(id)),
    edges: outEdges,
    roles,
    hidden: dependents.length - shownDeps.length,
  }
}
