import { at } from '../lib/anim.js'
import { depthFor, rgba } from '../lib/draw.js'
import { mulberry32, smooth } from '../lib/math.js'
import { Plane } from '../lib/plane.js'
import { cameraFrom } from '../lib/scene3d.js'
import { drawVoid } from './void.js'

// The ordered lanes, close up, are a session trace: one lane per agent, one
// block per step, and a replay head lighting each step as it passes.
const KINDS = {
  Read: [110, 206, 255], Edit: [100, 232, 196], Bash: [255, 188, 110], Grep: [140, 170, 255],
  Test: [120, 240, 170], Think: [170, 150, 255], Agent: [236, 224, 255],
}
const LANES = ['主代理', '子代理 · 探索', '子代理 · 实现', '验证者', '主代理 · 收尾']

function build(seed) {
  const rnd = mulberry32(seed)
  const pick = (w) => { let r = rnd() * w.reduce((s, x) => s + x[1], 0); for (const [k, n] of w) { if ((r -= n) <= 0) return k } return w[0][0] }
  const weights = [['Read', 5], ['Edit', 3], ['Bash', 2], ['Grep', 2], ['Think', 1.5], ['Test', 1.2]]
  return LANES.map((name, lane) => {
    const spans = []
    let u = lane === 0 ? 0.2 : 1.2 + lane * 0.9 + rnd() * 0.6
    const end = lane === 0 ? 40 : u + 9 + rnd() * 10
    while (u < end) {
      const kind = lane === 3 && rnd() < 0.5 ? 'Test' : pick(weights)
      const len = kind === 'Think' ? 0.5 + rnd() * 0.8 : kind === 'Bash' || kind === 'Test' ? 0.7 + rnd() * 1.6 : 0.25 + rnd() * 0.9
      spans.push({ kind, u, len })
      u += len + 0.08 + rnd() * 0.25
    }
    return { name, spans, start: spans[0].u }
  })
}

export default {
  grade: 'night',
  render(f, stage) {
    const p = f.p
    drawVoid(f, stage, { dust: 0.05 })
    const cam = cameraFrom(f, p.cam ?? {
      pos: { from: [-1.8, 0.9, -11.8], to: [2.6, 0.8, -11.2], t0: 0, t1: f.dur, ease: 'inOut' },
      target: { from: [6.0, -0.75, 1.6], to: [10.2, -0.75, 2.8], t0: 0, t1: f.dur, ease: 'inOut' }, fov: 42,
    })
    const depth = depthFor(f.W, f.H)
    const plane = new Plane(f, cam, { origin: [0, 1.6, 0], ex: [1, 0, 0.3], ey: [0, -1, 0] })
    const style = { focus: at(p.focus ?? 12.5, f.t), aperture: p.aperture ?? 0.5 }
    const lanes = stage.memo(`trace-${f.seed}`, () => build(f.seed))
    const head = at(p.head ?? { from: 2, to: 17, t0: 0, t1: f.dur, ease: 'linear' }, f.t)
    const laneH = 0.72, barH = 0.34

    for (let s = 0; s <= 40; s += 1) {
      const major = s % 5 === 0
      plane.line(depth, s, -0.5, s, lanes.length * laneH, [120, 160, 200], major ? 0.08 : 0.035, { width: 1.5, style })
      if (major) plane.text(depth, `02:${String(14 + s / 5).padStart(2, '0')}:00`, s + 0.06, -0.62, 0.11, [150, 180, 210], 0.55, { style })
    }
    lanes.forEach((lane, i) => {
      const v = i * laneH
      plane.line(depth, lane.start - 0.3, v + barH / 2, 40, v + barH / 2, [90, 150, 210], 0.14, { width: 1.5, style })
      plane.text(depth, lane.name, lane.start - 0.45, v + barH * 0.72, 0.15, [200, 220, 240], 0.7, { style, family: 'PingFang SC', weight: 300, align: 'right' })
      for (const sp of lane.spans) {
        const done = smooth(sp.u - 0.2, sp.u + 0.1, head)
        const c = KINDS[sp.kind]
        const hot = Math.exp(-Math.abs(head - (sp.u + sp.len / 2)) * 1.6)
        plane.rect(depth, sp.u, v, sp.u + sp.len, v + barH, {
          fill: rgba(c, 0.07 + 0.18 * done + 0.25 * hot), stroke: rgba(c, 0.25 + 0.6 * done), width: 2.2, style,
        })
        if (sp.len > 0.55) {
          const label = sp.kind === 'Test' ? 'pytest ✓' : sp.kind
          plane.text(depth, label, sp.u + 0.09, v + barH * 0.7, 0.12, done > 0.5 ? [236, 246, 255] : c, 0.35 + 0.6 * done, { style })
        }
      }
      if (i > 0 && i < lanes.length) {
        const from = lanes[0].spans.find((sp) => sp.u >= lane.start - 1.2) ?? lanes[0].spans[0]
        const q = [[from.u + from.len / 2, barH], [lane.start - 0.1, v + barH / 2]]
        plane.poly(depth, q, { stroke: rgba(KINDS.Agent, 0.35 * smooth(lane.start - 1, lane.start, head)), width: 2, style })
      }
    })
    plane.rect(depth, head - 0.012, -0.5, head + 0.012, lanes.length * laneH, { fill: rgba([255, 236, 214], 0.9), style })
    plane.rect(depth, head - 0.18, -0.5, head, lanes.length * laneH, { fill: rgba([255, 220, 180], 0.035), style })
    depth.flush(stage.emit)
    return { emitGain: p.gain ?? 1.4 }
  },
}

