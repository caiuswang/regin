import { depthFor, dot, font, glowPath, rgba } from '../lib/draw.js'
import { clamp, easeInOut, easeOut, lerp, smooth } from '../lib/math.js'
import { drawPlain, plainCamera } from './plain.js'

// Dawn, out of focus, behind one frosted card: the night's work summed up in
// four lines and one warm button. The cursor is the only hand in the film.
const PASS = [132, 238, 204]
const WARM = [255, 178, 98]
const INK = [255, 244, 230]

function cursorPath(x, y, s) {
  const p = new Path2D()
  const pts = [[0, 0], [0, 17], [4.2, 13.2], [7, 19.6], [9.6, 18.5], [6.9, 12.3], [12.4, 12.3]]
  pts.forEach(([px, py], i) => (i ? p.lineTo(x + px * s, y + py * s) : p.moveTo(x + px * s, y + py * s)))
  p.closePath()
  return p
}

function drawCard(f, stage, p, box, reveal, clickT) {
  const c = stage.over
  const { x, y, w, h } = box
  const pad = h * 0.12
  c.save()
  c.globalAlpha = reveal
  c.beginPath()
  c.roundRect(x, y, w, h, h * 0.06)
  c.fillStyle = 'rgba(12, 10, 16, 0.5)'
  c.fill()
  c.lineWidth = 2
  c.strokeStyle = 'rgba(255, 255, 255, 0.2)'
  c.stroke()
  const H = f.H
  let cy = y + pad
  dot(c, x + pad + H * 0.006, cy + H * 0.011, H * 0.007, PASS, 1)
  font(c, { family: 'PingFang SC', weight: 400, size: H * 0.022, tracking: 0.12 })
  c.textBaseline = 'middle'
  c.fillStyle = rgba(PASS, 0.95)
  c.fillText(p.status ?? '已完成 · 已验证', x + pad + H * 0.024, cy + H * 0.011)
  cy += H * 0.075
  font(c, { family: 'Songti SC', weight: 400, size: H * 0.042, tracking: 0.06 })
  c.fillStyle = rgba(INK, 1)
  c.fillText(p.title, x + pad, cy)
  cy += H * 0.05
  c.fillStyle = 'rgba(255, 255, 255, 0.14)'
  c.fillRect(x + pad, cy, w - pad * 2, 2)
  cy += H * 0.05
  for (const [label, value] of p.rows ?? []) {
    font(c, { family: 'PingFang SC', weight: 400, size: H * 0.023, tracking: 0.2 })
    c.fillStyle = 'rgba(255, 206, 160, 0.95)'
    c.fillText(label, x + pad, cy)
    font(c, { family: 'PingFang SC', weight: 400, size: H * 0.023, tracking: 0.06 })
    c.fillStyle = 'rgba(255, 255, 255, 0.94)'
    c.fillText(value, x + pad + H * 0.11, cy)
    cy += H * 0.05
  }
  const bw = H * 0.15, bh = H * 0.054
  const bx = x + w - pad - bw, by = y + h - pad * 0.8 - bh
  const pressed = clickT != null ? Math.exp(-Math.max(0, f.t - clickT) * 4) * (f.t >= clickT ? 1 : 0) : 0
  c.beginPath()
  c.roundRect(bx, by, bw, bh, bh / 2)
  const g = c.createLinearGradient(bx, by, bx, by + bh)
  g.addColorStop(0, rgba([255, 196, 124], 1))
  g.addColorStop(1, rgba([244, 150, 72], 1))
  c.fillStyle = g
  c.fill()
  font(c, { family: 'PingFang SC', weight: 500, size: H * 0.024, tracking: 0.3 })
  c.textAlign = 'center'
  c.fillStyle = 'rgba(52, 26, 8, 0.95)'
  c.fillText(p.button ?? '合并', bx + bw / 2 + H * 0.003, by + bh / 2)
  const sw = H * 0.17
  c.beginPath()
  c.roundRect(bx - sw - H * 0.016, by, sw, bh, bh / 2)
  c.lineWidth = 2
  c.strokeStyle = 'rgba(255, 255, 255, 0.28)'
  c.stroke()
  font(c, { family: 'PingFang SC', weight: 300, size: H * 0.022, tracking: 0.2 })
  c.fillStyle = 'rgba(255, 255, 255, 0.82)'
  c.fillText(p.secondary ?? '回放过程', bx - sw / 2 - H * 0.016 + H * 0.002, by + bh / 2)
  c.restore()
  const e = stage.emit
  e.save()
  e.globalCompositeOperation = 'lighter'
  e.beginPath()
  e.roundRect(bx, by, bw, bh, bh / 2)
  e.fillStyle = rgba(WARM, (0.16 + 0.5 * pressed) * reveal)
  e.fill()
  e.restore()
  return { button: { x: bx + bw / 2, y: by + bh / 2, w: bw, h: bh } }
}

export default {
  grade: 'dawn',
  render(f, stage) {
    const p = f.p
    drawPlain(f, stage, p.plain, plainCamera(f, p.plain))
    const { A } = stage
    const w = f.W * 0.5, h = A.h * 0.66
    const reveal = easeOut(clamp((f.t - 0.2) / 1.0))
    const box = { x: (f.W - w) / 2, y: A.y + (A.h - h) / 2 - A.h * 0.02 + (1 - reveal) * f.H * 0.02, w, h }
    stage.glass = {
      rect: [box.x / f.W, 1 - (box.y + h) / f.H, (box.x + w) / f.W, 1 - box.y / f.H],
      radius: h * 0.06, amount: 0.92 * reveal, tint: [0.5, 0.47, 0.5],
    }
    const clickT = p.click ?? 6.1
    const { button } = drawCard(f, stage, p, box, reveal, clickT)
    const move = easeInOut(clamp((f.t - (p.cursorFrom ?? 3.0)) / 2.4))
    const cx = lerp(f.W * 0.8, button.x + button.w * 0.12, move), cy = lerp(A.y + A.h * 0.9, button.y + button.h * 0.18, move)
    const press = f.t >= clickT ? 1 - 0.12 * Math.exp(-(f.t - clickT) * 8) : 1
    const s = f.H * 0.0036 * press
    if (f.t > (p.cursorFrom ?? 3.0) - 0.4) {
      const c = stage.over
      c.save()
      c.globalAlpha = smooth((p.cursorFrom ?? 3.0) - 0.4, p.cursorFrom ?? 3.0, f.t)
      const path = cursorPath(cx, cy, s)
      c.fillStyle = 'rgba(255, 250, 244, 0.97)'
      c.fill(path)
      c.lineWidth = f.H * 0.0012
      c.strokeStyle = 'rgba(20, 14, 10, 0.9)'
      c.stroke(path)
      c.restore()
    }
    if (f.t >= clickT) {
      const since = f.t - clickT
      const e = depthFor(f.W, f.H)
      const ring = new Path2D()
      ring.arc(button.x, button.y, f.H * (0.03 + since * 0.5), 0, Math.PI * 2)
      glowPath(e.at(0), ring, WARM, 3, { alpha: Math.max(0, 1 - since / 1.6) })
      e.flush(stage.emit)
    }
    return { emitGain: 1.3 }
  },
}
