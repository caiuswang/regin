import { at } from '../lib/anim.js'
import { depthFor, font, rgba } from '../lib/draw.js'
import { add, clamp, easeInOut, easeOut, lerp, mulberry32, norm, scale, smooth, sub } from '../lib/math.js'
import { arc, cameraFrom, node, ring, strokeCurve } from '../lib/scene3d.js'
import { drawVoid } from './void.js'

// One sentence becomes a structure: the sparks gather into a warm root, the
// root splits into slices, each slice hands its tasks to cold comets.
const WARM = [255, 172, 92]
const HOT = [255, 228, 186]
const PALE = [255, 236, 212]
const CYAN = [112, 214, 255]
const CYAN_DIM = [64, 140, 205]

function build(seed, p) {
  const rnd = mulberry32(seed)
  const root = p.root ?? [0, 0.35, 0.2]
  const tRoot = p.tRoot ?? 2.8
  const jitter = (k) => [(rnd() - 0.5) * k, (rnd() - 0.5) * k, (rnd() - 0.5) * k]
  const chars = [...(p.text ?? '让每一次失败，都说得出原因。')]
  const glyphs = chars.map((ch, i) => ({
    ch, pos: [lerp(-3.2, 3.2, i / (chars.length - 1)), -1.05, -0.4], tb: 0.3 + i * 0.075 + rnd() * 0.12,
  }))
  const sparks = glyphs.flatMap((g) => Array.from({ length: /[，。]/.test(g.ch) ? 2 : 7 }, () => {
    const a = add(g.pos, [(rnd() - 0.5) * 0.3, (rnd() - 0.2) * 0.35, (rnd() - 0.5) * 0.2])
    const t0 = g.tb + rnd() * 0.35
    return { curve: arc(a, root, [(rnd() - 0.5) * 2.6, 0.5 + rnd() * 1.1, (rnd() - 0.5) * 1.4]), t0, dur: tRoot - 0.25 * rnd() - t0 }
  }))
  const K = p.slices ?? 6
  const slices = Array.from({ length: K }, (_, k) => {
    const a = (k / K) * Math.PI * 2 + 0.42
    const pos = add(root, [Math.cos(a) * 3.0, Math.sin(a) * 0.95, Math.sin(a + 0.8) * 1.5])
    const t0 = tRoot + 0.3 + k * 0.13
    const out = norm(sub(pos, root))
    const n = 3 + Math.floor(rnd() * 3)
    const tasks = Array.from({ length: n }, (_, j) => {
      const spread = (j - (n - 1) / 2) * 0.55 + (rnd() - 0.5) * 0.2
      const side = norm([-out[1], out[0], 0.35 * (rnd() - 0.5)])
      const tpos = add(add(pos, scale(out, 1.2 + rnd() * 0.8)), add(scale(side, spread), jitter(0.5)))
      const tt = t0 + 0.9 + j * 0.28 + rnd() * 0.35
      const period = 1.0 + rnd() * 0.9
      return {
        pos: tpos, curve: arc(pos, tpos, jitter(0.6)), t0: tt, period,
        comets: Array.from({ length: 3 }, (_, i) => ({ t0: tt + 0.35 + i * (period / 3) + rnd() * 0.2 })),
      }
    })
    return { pos, curve: arc(root, pos, jitter(1.1)), t0, tasks }
  })
  return { root, tRoot, glyphs, sparks, slices }
}

export default {
  grade: 'night',
  render(f, stage) {
    const p = f.p
    drawVoid(f, stage, { dust: 0.07 })
    const cam = cameraFrom(f, p.cam ?? { pos: { from: [0, 0.3, -10.2], to: [0.9, 0.8, -11.8], t0: 0, t1: f.dur }, target: [0, 0.3, 0.3], fov: 44 })
    const depth = depthFor(f.W, f.H)
    const g = stage.memo(`bloom-${f.seed}`, () => build(f.seed, p))
    const focus = at(p.focus ?? 11, f.t), aperture = p.aperture ?? 0.55
    const style = { focus, aperture }

    for (const gl of g.glyphs) {
      const a = 1 - smooth(gl.tb, gl.tb + 0.55, f.t)
      if (a <= 0) continue
      const q = cam.project(gl.pos)
      if (!q) continue
      const ctx = depth.at(0)
      const size = 0.34 * q.k
      font(ctx, { family: 'Songti SC', weight: 400, size, tracking: 0 })
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillStyle = rgba([255, 206, 150], a * (1 + 0.8 * smooth(gl.tb - 0.2, gl.tb, f.t)) * 0.9)
      ctx.fillText(gl.ch, q.x, q.y)
    }
    for (const s of g.sparks) {
      const u = clamp((f.t - s.t0) / s.dur)
      if (u <= 0 || u >= 1) continue
      const head = easeInOut(u)
      strokeCurve(f, depth, cam, s.curve, Math.max(0, head - 0.22), head, { ...style, color: WARM, width: 0.009, alpha: 0.85, segs: 12 })
      node(f, depth, cam, s.curve(head), { ...style, r: 0.018, color: HOT, halo: 4 })
    }
    const ign = smooth(g.tRoot - 0.15, g.tRoot + 0.35, f.t)
    if (ign > 0) {
      const flash = 1 + 0.8 * Math.exp(-Math.max(0, f.t - g.tRoot) * 3)
      node(f, depth, cam, g.root, { ...style, r: 0.07 * flash, color: HOT, alpha: ign, halo: 4 })
      ring(f, depth, cam, g.root, { ...style, r: 0.2 + Math.max(0, f.t - g.tRoot) * 1.6, color: WARM, alpha: ign * Math.exp(-Math.max(0, f.t - g.tRoot) * 3.2), width: 2 })
    }
    for (const s of g.slices) {
      const u = easeOut(clamp((f.t - s.t0) / 0.9))
      if (u <= 0) continue
      strokeCurve(f, depth, cam, s.curve, 0, u, { ...style, color: PALE, width: 0.014, alpha: 0.8 })
      if (u >= 1) node(f, depth, cam, s.pos, { ...style, r: 0.05, color: PALE, halo: 4 })
      for (const task of s.tasks) {
        const v = easeOut(clamp((f.t - task.t0) / 0.7))
        if (v <= 0) continue
        strokeCurve(f, depth, cam, task.curve, 0, v, { ...style, color: CYAN_DIM, width: 0.006, alpha: 0.6 })
        const arrived = task.comets.some((c) => f.t - c.t0 >= task.period)
        if (v >= 1) node(f, depth, cam, task.pos, { ...style, r: arrived ? 0.038 : 0.026, color: arrived ? CYAN : CYAN_DIM, alpha: arrived ? 1 : 0.7, halo: arrived ? 4 : 2.5 })
        for (const c of task.comets) {
          const w = (f.t - c.t0) / task.period
          if (w < 0) continue
          const head = w % 1
          strokeCurve(f, depth, cam, task.curve, Math.max(0, head - 0.3), head, { ...style, color: CYAN, width: 0.008, alpha: 0.9, segs: 10 })
          node(f, depth, cam, task.curve(head), { ...style, r: 0.016, color: [220, 246, 255], halo: 5 })
        }
      }
    }
    depth.flush(stage.emit)
    return { emitGain: p.gain ?? 1.6 }
  },
}
