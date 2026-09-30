import { at } from '../lib/anim.js'
import { depthFor } from '../lib/draw.js'
import { clamp, easeInOut, mulberry32, smooth } from '../lib/math.js'
import { arc, cameraFrom, node, ring, strokeCurve } from '../lib/scene3d.js'
import { drawVoid } from './void.js'

// The work as a graph, left (the feature) to right (the steps). A red spark
// at the far end runs back along exactly one path to the node that caused it.
const DIM = [70, 142, 200]
const NODE = [130, 200, 245]
const RED = [255, 70, 96]
const RED_HOT = [255, 196, 200]
const GOLD = [255, 214, 150]

function build(seed, p) {
  const rnd = mulberry32(seed)
  const cols = p.cols ?? [1, 4, 9, 15, 22]
  const nodes = cols.map((n, c) => Array.from({ length: n }, (_, i) => ({
    c, pos: [-5.4 + c * 2.7, n === 1 ? 0 : (i / (n - 1) - 0.5) * (1.4 + c * 0.75) + (rnd() - 0.5) * 0.25, (rnd() - 0.5) * (0.6 + c * 0.35)],
    parents: [],
  })))
  const edges = []
  for (let c = 1; c < cols.length; c++) {
    for (const n of nodes[c]) {
      const ranked = [...nodes[c - 1]].sort((a, b) => Math.abs(a.pos[1] - n.pos[1]) - Math.abs(b.pos[1] - n.pos[1]))
      const k = 1 + (rnd() < 0.35 ? 1 : 0)
      for (const par of ranked.slice(0, k)) {
        const e = { a: par, b: n, curve: arc(par.pos, n.pos, [0, (rnd() - 0.5) * 0.5, (rnd() - 0.5) * 0.5]) }
        n.parents.push(e)
        edges.push(e)
      }
    }
  }
  const last = nodes.at(-1)
  let cur = last[Math.floor(last.length * (p.defectAt ?? 0.62))]
  const path = []
  while (cur.parents.length) { path.push(cur.parents[0]); cur = cur.parents[0].a }
  return { nodes, edges, path, defect: path[0].b, root: cur }
}

export default {
  grade: 'night',
  render(f, stage) {
    const p = f.p
    drawVoid(f, stage, { dust: 0.06 })
    const cam = cameraFrom(f, p.cam ?? { pos: { from: [2.2, 1.7, -12.6], to: [0.4, 1.2, -11.2], t0: 0, t1: f.dur }, target: { from: [0.8, 0, 0], to: [-0.9, 0, 0], t0: 0, t1: f.dur }, fov: 50 })
    const depth = depthFor(f.W, f.H)
    const g = stage.memo(`source-${f.seed}`, () => build(f.seed, p))
    const style = { focus: at(p.focus ?? 11.5, f.t), aperture: p.aperture ?? 0.7 }
    const t0 = p.spark ?? 1.0, per = p.perEdge ?? 0.62
    const onPath = new Set(g.path)
    const lit = g.path.map((_, i) => clamp((f.t - t0 - 0.35 - i * per) / per))
    const dimAll = 1 - 0.55 * smooth(t0, t0 + 1.5, f.t)
    for (const e of g.edges) {
      if (onPath.has(e)) continue
      strokeCurve(f, depth, cam, e.curve, 0, 1, { ...style, color: DIM, width: 0.007, alpha: 0.5 * dimAll, halo: 0.6, segs: 16 })
    }
    for (const col of g.nodes) {
      for (const n of col) {
        if (n === g.root) continue
        node(f, depth, cam, n.pos, { ...style, r: 0.04, color: NODE, alpha: 0.75 * dimAll, halo: 2.5 })
      }
    }
    g.path.forEach((e, i) => {
      const u = easeInOut(lit[i])
      strokeCurve(f, depth, cam, e.curve, 0, 1, { ...style, color: DIM, width: 0.008, alpha: 0.5 * dimAll, halo: 0.6, segs: 16 })
      if (u <= 0) return
      strokeCurve(f, depth, cam, e.curve, 1 - u, 1, { ...style, color: RED, width: 0.014, alpha: 0.95, segs: 20 })
      if (u < 1) node(f, depth, cam, e.curve(1 - u), { ...style, r: 0.03, color: RED_HOT, halo: 6 })
      node(f, depth, cam, e.b.pos, { ...style, r: 0.05, color: RED, halo: 4 })
    })
    const spark = smooth(t0 - 0.2, t0 + 0.2, f.t)
    if (spark > 0) {
      const flick = 0.8 + 0.2 * Math.sin(f.t * 31)
      node(f, depth, cam, g.defect.pos, { ...style, r: 0.07 * flick, color: RED_HOT, alpha: spark, halo: 6 })
    }
    const reached = lit.at(-1) >= 1
    node(f, depth, cam, g.root.pos, { ...style, r: 0.09, color: reached ? RED_HOT : GOLD, halo: 5 })
    if (reached) {
      const since = f.t - (t0 + 0.35 + g.path.length * per)
      for (let k = 0; k < 3; k++) {
        const s = (since - k * 0.45)
        if (s < 0) continue
        const ph = (s % 1.35) / 1.35
        ring(f, depth, cam, g.root.pos, { ...style, r: 0.14 + ph * 0.55, color: RED, alpha: (1 - ph) * 0.9, width: 3 })
      }
      ring(f, depth, cam, g.root.pos, { ...style, r: 0.2, color: RED_HOT, alpha: 0.9, width: 2.2 })
    }
    depth.flush(stage.emit)
    return { emitGain: p.gain ?? 1.6 }
  },
}
