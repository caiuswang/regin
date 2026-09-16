/**
 * Lay `/api/tasks/graph` out as topological layers for a plain-SVG render.
 *
 * A node sits one layer below the deepest thing it depends on, so every edge
 * points downward and the picture reads as "these finish before those". No
 * graph library: the shape is a handful of boxes and straight lines, and the
 * server already refuses a cycle at write time.
 *
 * The relaxation pass is still capped at `nodes.length`, because the layout is
 * fed by whatever the API returned — a cycle that reached this function would
 * otherwise spin forever, and a drawing with one edge pointing the wrong way
 * is a better failure than a hung tab.
 */

export const NODE_W = 172
export const NODE_H = 48
export const GAP_X = 20
export const GAP_Y = 46
export const PAD = 14

const TITLE_CHARS = 22

function depMap(nodes, edges) {
  const deps = new Map(nodes.map(n => [n.id, []]))
  for (const edge of edges) {
    if (deps.has(edge.task_id) && deps.has(edge.depends_on_id)) {
      deps.get(edge.task_id).push(edge.depends_on_id)
    }
  }
  return deps
}

function layerOf(nodes, deps) {
  const layer = new Map(nodes.map(n => [n.id, 0]))
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false
    for (const node of nodes) {
      const want = Math.max(0, ...deps.get(node.id).map(d => layer.get(d) + 1))
      if (want > layer.get(node.id)) {
        layer.set(node.id, want)
        moved = true
      }
    }
    if (!moved) break
  }
  return layer
}

function rows(nodes, layer) {
  const byLayer = new Map()
  for (const node of nodes) {
    const depth = layer.get(node.id)
    if (!byLayer.has(depth)) byLayer.set(depth, [])
    byLayer.get(depth).push(node)
  }
  return [...byLayer.entries()].sort((a, b) => a[0] - b[0])
}

function clipTitle(title) {
  const text = String(title || '')
  return text.length > TITLE_CHARS ? `${text.slice(0, TITLE_CHARS - 1)}…` : text
}

function place(nodes, layer) {
  const placed = new Map()
  for (const [depth, row] of rows(nodes, layer)) {
    row.sort((a, b) => a.id - b.id)
    row.forEach((node, index) => {
      placed.set(node.id, {
        ...node,
        label: clipTitle(node.title),
        layer: depth,
        x: PAD + index * (NODE_W + GAP_X),
        y: PAD + depth * (NODE_H + GAP_Y),
      })
    })
  }
  return placed
}

function lines(edges, placed) {
  const out = []
  for (const edge of edges) {
    const from = placed.get(edge.depends_on_id)
    const to = placed.get(edge.task_id)
    if (!from || !to) continue
    out.push({
      key: `${edge.task_id}-${edge.depends_on_id}`,
      x1: from.x + NODE_W / 2, y1: from.y + NODE_H,
      x2: to.x + NODE_W / 2, y2: to.y,
    })
  }
  return out
}

/** `{ nodes, edges, width, height }` in SVG user units; empty input is 0×0. */
export function layoutGraph(nodes = [], edges = []) {
  if (!nodes.length) return { nodes: [], edges: [], width: 0, height: 0 }
  const deps = depMap(nodes, edges)
  const layer = layerOf(nodes, deps)
  const placed = place(nodes, layer)
  const boxes = [...placed.values()]
  return {
    nodes: boxes,
    edges: lines(edges, placed),
    width: Math.max(...boxes.map(b => b.x + NODE_W)) + PAD,
    height: Math.max(...boxes.map(b => b.y + NODE_H)) + PAD,
  }
}
