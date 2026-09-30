import { at } from '../lib/anim.js'
import { depthFor, dot, glowPath, rgba } from '../lib/draw.js'
import { lerp, mulberry32, smooth } from '../lib/math.js'
import { drawVoid } from './void.js'

// Hundreds of threads of work, tangled; a vertical beam sweeps across and
// every thread behind it lies down into an ordered lane with visible steps.
const CHAOS = [[120, 208, 255], [196, 232, 255], [84, 140, 255]]
const ERROR = [255, 84, 112]
const LANE = [112, 212, 255]
const BEAM = [255, 238, 214]

function build(seed, A, W, p) {
  const rnd = mulberry32(seed)
  const lanes = p.lanes ?? 11, per = p.perLane ?? 8
  const fils = []
  for (let lane = 0; lane < lanes; lane++) {
    for (let k = 0; k < per; k++) {
      fils.push({
        yOrder: A.y + A.h * (0.13 + (0.74 * (lane + 0.5)) / lanes) + (k - (per - 1) / 2) * A.h * 0.0032,
        center: A.y + A.h * (0.18 + 0.64 * rnd()),
        waves: Array.from({ length: 3 }, (_, i) => ({
          amp: A.h * (0.04 + 0.2 * rnd()) / (i + 1) ** 0.6, freq: ((0.5 + 3.4 * rnd()) * Math.PI * 2) / W,
          phase: rnd() * Math.PI * 2, speed: (rnd() - 0.5) * 1.6,
        })),
        plane: rnd(),
        color: rnd() < 0.05 ? ERROR : CHAOS[Math.floor(rnd() * 3)],
      })
    }
  }
  const steps = []
  for (let lane = 0; lane < lanes; lane++) {
    const y = A.y + A.h * (0.13 + (0.74 * (lane + 0.5)) / lanes)
    let x = W * (0.02 + rnd() * 0.05)
    while (x < W) {
      steps.push({ x, y, w: W * (0.012 + rnd() * 0.05) })
      x += W * (0.03 + rnd() * 0.07)
    }
  }
  return { fils, steps }
}

export function drawWeave(f, stage, depth, { beam = -1, band = 0.07, lanes, perLane, alpha = 1, blurBoost = 0, speed = 1 } = {}) {
  const { A } = stage
  const W = f.W
  const g = stage.memo(`weave-${f.seed}-${lanes}-${perLane}`, () => build(f.seed, A, W, { lanes, perLane }))
  const bx = beam * W, bw = band * W
  const t = f.t * speed
  const N = 200
  for (const fil of g.fils) {
    const left = [], right = []
    for (let i = 0; i <= N; i++) {
      const x = lerp(-0.03 * W, 1.03 * W, i / N)
      const s = smooth(bx + bw, bx - bw, x)
      let yc = fil.center
      for (const w of fil.waves) yc += w.amp * Math.sin(w.freq * x + w.phase + t * w.speed)
      const q = { x, y: lerp(yc, fil.yOrder, s) }
      if (s > 0.5) left.push(q)
      if (s <= 0.55) right.push(q)
    }
    const blur = (fil.plane < 0.2 ? 14 : fil.plane > 0.88 ? 8 : 0) + blurBoost
    const ctx = depth.at(blur)
    const px = f.H * (fil.plane > 0.88 ? 0.0011 : 0.0007)
    const path = (pts) => { const p = new Path2D(); pts.forEach((q, i) => (i ? p.lineTo(q.x, q.y) : p.moveTo(q.x, q.y))); return p }
    if (right.length > 1) glowPath(ctx, path(right), fil.color, px, { alpha: 0.3 * alpha * (fil.plane < 0.2 ? 0.6 : 1), halo: 0.8 })
    if (left.length > 1) glowPath(ctx, path(left), LANE, px * 0.9, { alpha: 0.42 * alpha, halo: 0.6 })
  }
  if (beam > 0) {
    for (const s of g.steps) {
      const shown = smooth(bx - bw * 0.2, bx - bw * 1.2, s.x)
      if (shown <= 0) continue
      const pop = 1 + 0.8 * Math.exp(-Math.max(0, bx - bw * 0.2 - s.x) / (W * 0.02))
      const ctx = depth.at(0)
      ctx.save()
      ctx.globalCompositeOperation = 'lighter'
      ctx.fillStyle = rgba([196, 238, 255], 0.2 * shown)
      ctx.fillRect(s.x, s.y - f.H * 0.004, s.w, f.H * 0.008)
      ctx.restore()
      dot(ctx, s.x, s.y, f.H * 0.0042 * pop, [236, 250, 255], shown)
    }
  }
  return { bx }
}

function drawBeam(f, stage, depth, bx) {
  const { A } = stage
  const ctx = depth.at(0)
  const path = new Path2D()
  path.moveTo(bx, A.y)
  path.lineTo(bx, A.y + A.h)
  glowPath(ctx, path, BEAM, f.H * 0.0016, { alpha: 1, halo: 2.6 })
}

export default {
  grade: 'night',
  render(f, stage) {
    const p = f.p
    drawVoid(f, stage, { dust: 0.05 })
    const depth = depthFor(f.W, f.H)
    const beam = at(p.beam ?? -1, f.t)
    const { bx } = drawWeave(f, stage, depth, { beam, band: p.band, lanes: p.lanes, perLane: p.perLane, speed: p.speed ?? 1 })
    if (beam > 0 && beam < 1.05) drawBeam(f, stage, depth, bx)
    depth.flush(stage.emit)
    return { emitGain: p.gain ?? 1.5 }
  },
}
