import { at } from '../lib/anim.js'
import { depthFor, glowPath, rgba } from '../lib/draw.js'
import { clamp, easeInOut, lerp, mix3, mulberry32, smooth } from '../lib/math.js'
import { cameraFrom, coc, node } from '../lib/scene3d.js'
import { drawVoid } from './void.js'

// A pit in the ground, and every lesson ever learned lying in it as a small
// gold shard. A mistake falls in and becomes one; later a new agent heading
// for the same pit is turned aside by the shard that rises to meet it.
const GOLD = [255, 198, 128]
const RED = [255, 76, 96]
const CYAN = [112, 214, 255]
const R = 1.7

function build(seed) {
  const rnd = mulberry32(seed)
  return Array.from({ length: 1600 }, (_, k) => ({
    th: k * 2.39996 + rnd() * 0.3, y: -0.28 - k * 0.0105 - rnd() * 0.05, r: R * (0.5 + 0.44 * Math.sqrt(rnd())),
    rot: rnd() * Math.PI, b: 0.3 + 0.7 * rnd() ** 2,
  }))
}

function shard(f, depth, cam, pos, rot, size, color, alpha, style) {
  const c = Math.cos(rot) * size, s = Math.sin(rot) * size
  const corners = [[c, s * 0.6], [-s, c * 0.6], [-c, -s * 0.6], [s, -c * 0.6]].map(([dx, dz]) => cam.project([pos[0] + dx, pos[1], pos[2] + dz]))
  if (corners.some((q) => !q)) return
  const ctx = depth.at(coc(f, corners[0].z, style.focus, style.aperture, 40))
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  ctx.fillStyle = rgba(color, alpha)
  ctx.beginPath()
  corners.forEach((q, i) => (i ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y)))
  ctx.closePath()
  ctx.fill()
  ctx.restore()
}

function circle(f, depth, cam, r, y, color, alpha, width, style) {
  const pts = []
  for (let i = 0; i <= 96; i++) {
    const a = (i / 96) * Math.PI * 2
    const q = cam.project([Math.cos(a) * r, y, Math.sin(a) * r])
    if (q) pts.push(q)
  }
  if (pts.length < 2) return
  const path = new Path2D()
  pts.forEach((q, i) => (i ? path.lineTo(q.x, q.y) : path.moveTo(q.x, q.y)))
  glowPath(depth.at(coc(f, pts[0].z, style.focus, style.aperture, 40)), path, color, width, { alpha })
}

function cometPath(s) {
  if (s < 0.3) return [lerp(-8, -2.9, s / 0.3), 0.05, lerp(-1.8, 0, s / 0.3)]
  if (s < 0.7) {
    const a = Math.PI * (1 - (s - 0.3) / 0.4)
    return [Math.cos(a) * 2.9, 0.05, Math.sin(a) * 2.9]
  }
  return [lerp(2.9, 8, (s - 0.7) / 0.3), 0.05, lerp(0, -1.2, (s - 0.7) / 0.3)]
}

export default {
  grade: 'night',
  render(f, stage) {
    const p = f.p
    drawVoid(f, stage, { dust: 0.04 })
    const cam = cameraFrom(f, p.cam ?? {
      pos: { from: [0.3, 4.4, -2.6], to: [0.4, 4.4, -9.8], t0: 3.0, t1: 6.2 },
      target: { from: [0, -7, 0.9], to: [0, -0.9, 1.6], t0: 3.0, t1: 6.2 }, fov: 50,
    })
    const depth = depthFor(f.W, f.H)
    const style = { focus: at(p.focus ?? { from: 8.5, to: 10.5, t0: 3.0, t1: 6.2 }, f.t), aperture: p.aperture ?? 0.35 }
    const shards = stage.memo(`memory-${f.seed}`, () => build(f.seed))
    for (const s of shards) {
      const fade = Math.exp(s.y * 0.1)
      const tw = 0.85 + 0.15 * Math.sin(f.t * 1.3 + s.th * 3)
      shard(f, depth, cam, [Math.cos(s.th) * s.r, s.y, Math.sin(s.th) * s.r], s.rot + f.t * 0.05, 0.05, GOLD, 0.55 * s.b * fade * tw, style)
    }
    circle(f, depth, cam, R, 0, [255, 150, 110], 0.35, 2.2, style)
    for (const k of [1.35, 1.8, 2.5]) circle(f, depth, cam, R * k, 0, [90, 150, 210], 0.06, 1.5, style)

    const fall = clamp((f.t - (p.fall ?? 0.3)) / 2.6)
    if (fall > 0 && fall < 1.2) {
      const u = easeInOut(Math.min(1, fall))
      const y = lerp(2.2, -1.4, u), a = u * 5
      const pos = [Math.cos(a) * 0.6 * u, y, Math.sin(a) * 0.6 * u]
      const col = mix3(RED, GOLD, smooth(0.35, 0.65, u))
      node(f, depth, cam, pos, { ...style, r: 0.05, color: col, halo: 7 })
      shard(f, depth, cam, pos, a, 0.08, [255, 236, 200], 0.9, style)
    }

    const rise = smooth(p.rise ?? 3.8, (p.rise ?? 3.8) + 1.2, f.t)
    const beacon = [lerp(-0.4, -2.05, rise), lerp(-2.2, 0.75, rise), lerp(0.2, -0.95, rise)]
    if (rise > 0) {
      node(f, depth, cam, beacon, { ...style, r: 0.07, color: GOLD, halo: 8 })
      shard(f, depth, cam, beacon, f.t * 0.8, 0.1, [255, 236, 200], 0.95, style)
      const base = cam.project([beacon[0], 0, beacon[2]]), top = cam.project(beacon)
      if (base && top) {
        const path = new Path2D()
        path.moveTo(base.x, base.y)
        path.lineTo(top.x, top.y)
        glowPath(depth.at(coc(f, top.z, style.focus, style.aperture)), path, GOLD, 2, { alpha: 0.5 * rise })
      }
    }
    const go = clamp((f.t - (p.comet ?? 5.0)) / (p.cometDur ?? 3.4))
    if (go > 0) {
      const head = go, tail = Math.max(0, go - 0.35)
      const pts = []
      for (let i = 0; i <= 60; i++) {
        const q = cam.project(cometPath(lerp(tail, head, i / 60)))
        if (q) pts.push(q)
      }
      if (pts.length > 1) {
        const path = new Path2D()
        pts.forEach((q, i) => (i ? path.lineTo(q.x, q.y) : path.moveTo(q.x, q.y)))
        glowPath(depth.at(coc(f, pts.at(-1).z, style.focus, style.aperture)), path, CYAN, 3, { alpha: 0.9 })
        node(f, depth, cam, cometPath(head), { ...style, r: 0.05, color: [226, 248, 255], halo: 7 })
      }
    }
    depth.flush(stage.emit)
    return { emitGain: p.gain ?? 1.5 }
  },
}
