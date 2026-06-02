// Pure walkers over the PrimeVue TreeTable node shape used by the trace
// views. A "node" is `{ key, data: { span_id, ... }, children: [], leaf }`.
// All functions here are stateless recursions over a passed-in `nodes`
// array — no component state, no reactive refs — so they live outside the
// SFC and can be unit-tested directly.

// First node whose span_id matches, searching depth-first. null if absent.
export function findNodeBySpanId(nodes, spanId) {
  if (!nodes || !spanId) return null
  for (const n of nodes) {
    if (n.data?.span_id === spanId) return n
    const found = findNodeBySpanId(n.children || [], spanId)
    if (found) return found
  }
  return null
}

// Root → target chain. Caller uses every node except the last to know
// which ancestors must be expanded for the target row to render.
export function findNodePath(nodes, spanId) {
  if (!nodes || !spanId) return null
  for (const n of nodes) {
    if (n.data?.span_id === spanId) return [n]
    const sub = findNodePath(n.children || [], spanId)
    if (sub) return [n, ...sub]
  }
  return null
}

// The `key` of the node matching span_id (PrimeVue addresses rows by key,
// not span_id). null if absent.
export function findNodeKey(nodes, spanId) {
  if (!nodes || !spanId) return null
  for (const n of nodes) {
    if (n.data?.span_id === spanId) return n.key
    if (n.children && n.children.length) {
      const k = findNodeKey(n.children, spanId)
      if (k) return k
    }
  }
  return null
}

// Immutably replace the children of the node matching span_id, returning a
// new tree (structurally shared where unchanged). Returns the original
// `nodes` reference untouched when span_id isn't found, so callers can use
// identity to detect a no-op.
export function withNodeChildren(nodes, spanId, children) {
  let changed = false
  const next = (nodes || []).map((n) => {
    if (n?.data?.span_id === spanId) {
      changed = true
      return {
        ...n,
        children,
        leaf: children.length === 0,
      }
    }
    if (n.children?.length) {
      const updatedChildren = withNodeChildren(n.children, spanId, children)
      if (updatedChildren !== n.children) {
        changed = true
        return { ...n, children: updatedChildren }
      }
    }
    return n
  })
  return changed ? next : nodes
}
