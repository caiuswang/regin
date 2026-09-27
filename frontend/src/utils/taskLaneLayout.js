/**
 * Lay the Graph view out as feature lanes: one horizontal band per lane (in
 * the server's order), one column per topological depth shared by every lane,
 * so an edge always runs left to right and a cross-lane dependency still
 * reads "this finishes before that".
 *
 * Two tasks of one lane at one depth stack inside the band rather than
 * overlap. Depth relaxation is capped at the node count, as in
 * `taskGraphLayout.js`: the input is whatever the API returned, and a
 * wrong-way edge is a better failure than a hung tab.
 */

export const LANE_W = 196
export const NODE_W = 188
export const NODE_H = 72
export const COL_GAP = 24
export const ROW_GAP = 14
export const LANE_PAD = 20
// Tall enough for the lane head (ref, two title lines, bar, count) when the
// lane holds a single row of nodes.
export const LANE_MIN_H = 132

function depths(nodes, edges) {
  const ids = new Set(nodes.map(n => n.id))
  const deps = new Map(nodes.map(n => [n.id, []]))
  for (const e of edges) {
    if (ids.has(e.task_id) && ids.has(e.depends_on_id)) deps.get(e.task_id).push(e.depends_on_id)
  }
  const depth = new Map(nodes.map(n => [n.id, 0]))
  for (let pass = 0; pass < nodes.length; pass++) {
    let moved = false
    for (const n of nodes) {
      const want = Math.max(0, ...deps.get(n.id).map(d => depth.get(d) + 1))
      if (want > depth.get(n.id)) { depth.set(n.id, want); moved = true }
    }
    if (!moved) break
  }
  return depth
}

function laneKey(featureId) {
  return featureId == null ? 'none' : String(featureId)
}

function stackRows(nodes, depth) {
  const row = new Map()
  const used = new Map()
  for (const n of [...nodes].sort((a, b) => a.id - b.id)) {
    const slot = `${laneKey(n.feature_id)}:${depth.get(n.id)}`
    row.set(n.id, used.get(slot) || 0)
    used.set(slot, (used.get(slot) || 0) + 1)
  }
  return row
}

function bands(lanes, nodes, row) {
  const rowsIn = new Map(lanes.map(l => [laneKey(l.feature_id), 1]))
  for (const n of nodes) {
    const key = laneKey(n.feature_id)
    rowsIn.set(key, Math.max(rowsIn.get(key) || 1, row.get(n.id) + 1))
  }
  let top = 0
  return lanes.map(lane => {
    const rows = rowsIn.get(laneKey(lane.feature_id)) || 1
    const height = Math.max(LANE_MIN_H, LANE_PAD * 2 + rows * NODE_H + (rows - 1) * ROW_GAP)
    const band = { ...lane, key: laneKey(lane.feature_id), top, height }
    top += height
    return band
  })
}

function edgePath(from, to) {
  const x1 = from.x + NODE_W
  const y1 = from.y + NODE_H / 2
  const x2 = to.x
  const y2 = to.y + NODE_H / 2
  const mid = x1 + Math.max(COL_GAP / 2, (x2 - x1) / 2)
  return `M${x1},${y1} H${mid} V${y2} H${x2}`
}

/**
 * `{ lanes, nodes, edges, width, height }` in CSS pixels. `nodes` carry
 * `x`, `y` and `column`; `edges` carry an SVG `path`, `blocking`, and
 * `settled` (their source is done).
 */
export function layoutLanes(lanes = [], nodes = [], edges = []) {
  const depth = depths(nodes, edges)
  const row = stackRows(nodes, depth)
  const placedBands = bands(lanes, nodes, row)
  const top = new Map(placedBands.map(b => [b.key, b.top]))
  const placed = new Map(nodes.map(n => [n.id, {
    ...n,
    column: depth.get(n.id),
    x: LANE_W + COL_GAP + depth.get(n.id) * (NODE_W + COL_GAP),
    y: (top.get(laneKey(n.feature_id)) ?? 0) + LANE_PAD + row.get(n.id) * (NODE_H + ROW_GAP),
  }]))
  const drawn = edges
    .filter(e => placed.has(e.task_id) && placed.has(e.depends_on_id))
    .map(e => {
      const from = placed.get(e.depends_on_id)
      return {
        key: `${e.task_id}-${e.depends_on_id}`,
        kind: e.kind || 'requires',
        blocking: !!e.blocking,
        settled: from.status === 'done',
        path: edgePath(from, placed.get(e.task_id)),
      }
    })
  const columns = nodes.length ? Math.max(...[...depth.values()]) + 1 : 0
  const last = placedBands[placedBands.length - 1]
  return {
    lanes: placedBands,
    nodes: [...placed.values()],
    edges: drawn,
    width: LANE_W + COL_GAP + columns * (NODE_W + COL_GAP),
    height: last ? last.top + last.height : 0,
  }
}
