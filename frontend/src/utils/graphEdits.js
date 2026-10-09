/**
 * Pending dependency edits made on the lane graph, keyed by the task that
 * waits (`sourceId`). Pure: every function returns a new Map.
 *
 * `Map<sourceId, { add: DepEdge[], remove: Set<dependsOnId> }>`
 */

function entry(pending, sourceId) {
  const have = pending.get(sourceId)
  return have
    ? { add: [...have.add], remove: new Set(have.remove) }
    : { add: [], remove: new Set() }
}

function settle(pending, sourceId, next) {
  const out = new Map(pending)
  if (next.add.length || next.remove.size) out.set(sourceId, next)
  else out.delete(sourceId)
  return out
}

/** Toggle a drawn server edge between kept and removed. */
export function toggleRemove(pending, sourceId, dependsOnId) {
  const next = entry(pending, sourceId)
  if (next.remove.has(dependsOnId)) next.remove.delete(dependsOnId)
  else next.remove.add(dependsOnId)
  return settle(pending, sourceId, next)
}

/**
 * The second click of a pick. An edge already drawn between the two is marked
 * for removal; a pending add is withdrawn; anything else becomes a pending
 * add of `kind`.
 */
export function pick(pending, { sourceId, targetId, kind, existing }) {
  if (existing) return toggleRemove(pending, sourceId, targetId)
  const next = entry(pending, sourceId)
  const at = next.add.findIndex(e => e.id === targetId)
  if (at >= 0) next.add.splice(at, 1)
  else next.add.push({ id: targetId, kind })
  return settle(pending, sourceId, next)
}

/**
 * The full set to PUT for one source: its server edges, minus removals, plus
 * additions. An addition that names a dependency already held changes that
 * edge's kind instead of sending the pair twice.
 */
export function mergeDeps(serverEdges, change) {
  const out = new Map(serverEdges
    .filter(e => !change.remove.has(e.id))
    .map(e => [e.id, e.kind || 'requires']))
  for (const e of change.add) out.set(e.id, e.kind)
  return [...out].map(([id, kind]) => ({ id, kind }))
}

/** Pending adds as drawable `{ key, from, to, kind }` (from = dependency). */
export function pendingAdds(pending) {
  const out = []
  for (const [sourceId, change] of pending) {
    for (const e of change.add) {
      out.push({ key: `${sourceId}-${e.id}`, from: e.id, to: sourceId, kind: e.kind })
    }
  }
  return out
}

export function isRemoved(pending, sourceId, dependsOnId) {
  return !!pending.get(sourceId)?.remove.has(dependsOnId)
}
