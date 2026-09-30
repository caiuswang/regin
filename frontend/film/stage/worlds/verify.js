import { at } from '../lib/anim.js'
import { depthFor, glowPath, rgba } from '../lib/draw.js'
import { clamp, easeOut, smooth } from '../lib/math.js'
import { Plane } from '../lib/plane.js'
import { cameraFrom, coc, node } from '../lib/scene3d.js'
import { drawVoid } from './void.js'

// Two lights and one list. The cold one builds each acceptance item; the pale
// violet one reads every item again, independently, and sends one back.
const BUILD = [110, 214, 255]
const DOUBT = [214, 196, 255]
const PASS = [120, 240, 190]
const FAIL = [255, 72, 100]
const ITEMS = [
  'test_failure_names_its_cause', 'test_unknown_cause_carries_a_hint', 'test_recalled_lesson_suggests_fix',
  'test_parent_error_is_the_cause', 'test_explanation_survives_retry', 'test_span_path_is_stable', 'test_cost_is_attributed',
]

export default {
  grade: 'night',
  render(f, stage) {
    const p = f.p
    drawVoid(f, stage, { dust: 0.05 })
    const cam = cameraFrom(f, p.cam ?? { pos: { from: [-1.2, 0.8, -11.2], to: [-0.2, 0.5, -10.2], t0: 0, t1: f.dur }, target: [0.3, -0.2, 0.8], fov: 46 })
    const depth = depthFor(f.W, f.H)
    const plane = new Plane(f, cam, { origin: [-2.4, 1.35, 0], ex: [1, 0, 0.26], ey: [0, -1, 0.05] })
    const style = { focus: at(p.focus ?? 11, f.t), aperture: p.aperture ?? 0.55 }
    const failAt = p.failAt ?? 4
    const buildT = (i) => 0.4 + i * 0.42
    const scanT0 = p.scan ?? 4.9, scanPer = p.scanPer ?? 0.5
    const scanV = (f.t - scanT0) / scanPer
    const rowH = 0.5, stripH = 0.36, width = 5.6
    const builder = plane.at(-1.25, 1.6), doubter = plane.at(width + 1.25, 1.6)

    const doubtOn = smooth(scanT0 - 0.8, scanT0, f.t)
    node(f, depth, cam, builder, { ...style, r: 0.1, color: BUILD, halo: 6 })
    if (doubtOn > 0) node(f, depth, cam, doubter, { ...style, r: 0.1, color: DOUBT, alpha: doubtOn, halo: 6 })

    ITEMS.forEach((name, i) => {
      const v = i * rowH
      const grow = easeOut(clamp((f.t - buildT(i)) / 0.5))
      if (grow <= 0) return
      const scanned = scanV > i + 0.5
      const failed = scanned && i === failAt
      const fixed = failed && f.t > (p.fixAt ?? 99)
      const status = !scanned ? 'pending' : failed && !fixed ? 'fail' : 'pass'
      const tint = status === 'fail' ? FAIL : status === 'pass' ? PASS : BUILD
      const flash = scanned ? Math.exp(-Math.max(0, scanV - i - 0.5) * 2.2) : 0
      plane.rect(depth, 0, v, width * grow, v + stripH, {
        fill: rgba(tint, 0.05 + 0.1 * flash + (status === 'fail' ? 0.12 : 0)), stroke: rgba(tint, 0.3 + 0.4 * flash + (status === 'fail' ? 0.4 : 0)), width: 2, style,
      })
      if (grow < 1) return
      plane.text(depth, name, 0.62, v + stripH * 0.68, 0.13, [220, 234, 248], 0.8, { style })
      const mark = status === 'pass' ? '✓' : status === 'fail' ? '✗' : '○'
      plane.text(depth, mark, 0.22, v + stripH * 0.74, 0.19, tint, status === 'pending' ? 0.5 : 1, { style, family: 'PingFang SC', weight: 600 })
      const bq = cam.project(plane.at(0, v + stripH / 2)), rq = cam.project(builder)
      if (bq && rq && f.t < buildT(i) + 0.9) {
        const k = 1 - clamp((f.t - buildT(i)) / 0.9)
        const path = new Path2D()
        path.moveTo(rq.x, rq.y)
        path.lineTo(bq.x, bq.y)
        glowPath(depth.at(coc(f, bq.z, style.focus, style.aperture)), path, BUILD, 2, { alpha: 0.5 * k })
      }
      if (failed && !fixed) {
        const since = f.t - (scanT0 + (i + 0.5) * scanPer)
        const u = clamp(since / 0.9)
        const a = cam.project(plane.at(0, v + stripH / 2)), b = cam.project(builder)
        if (a && b) {
          const path = new Path2D()
          path.moveTo(a.x, a.y)
          path.quadraticCurveTo((a.x + b.x) / 2, Math.max(a.y, b.y) + f.H * 0.06, a.x + (b.x - a.x) * u, a.y + (b.y - a.y) * u)
          glowPath(depth.at(coc(f, a.z, style.focus, style.aperture)), path, FAIL, 3, { alpha: 0.9 })
        }
      }
    })
    if (scanV > -0.5 && scanV < ITEMS.length + 0.5) {
      const v = clamp(scanV, 0, ITEMS.length) * rowH
      const l = cam.project(plane.at(-0.2, v)), r = cam.project(plane.at(width + 0.2, v)), o = cam.project(doubter)
      if (l && r && o) {
        const ctx = depth.at(coc(f, l.z, style.focus, style.aperture))
        const scan = new Path2D()
        scan.moveTo(l.x, l.y)
        scan.lineTo(r.x, r.y)
        glowPath(ctx, scan, DOUBT, 2.6, { alpha: 0.95, halo: 1.6 })
        ctx.save()
        ctx.globalCompositeOperation = 'lighter'
        const g = ctx.createLinearGradient(o.x, o.y, (l.x + r.x) / 2, (l.y + r.y) / 2)
        g.addColorStop(0, rgba(DOUBT, 0.1))
        g.addColorStop(1, rgba(DOUBT, 0.02))
        ctx.fillStyle = g
        ctx.beginPath()
        ctx.moveTo(o.x, o.y)
        ctx.lineTo(l.x, l.y)
        ctx.lineTo(r.x, r.y)
        ctx.closePath()
        ctx.fill()
        ctx.restore()
      }
    }
    depth.flush(stage.emit)
    return { emitGain: p.gain ?? 1.45 }
  },
}
