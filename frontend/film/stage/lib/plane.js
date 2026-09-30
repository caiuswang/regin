import { rgba } from './draw.js'
import { add, norm, scale } from './math.js'
import { coc } from './scene3d.js'

// A flat sheet in 3D (u along ex, v along ey, both in world units) that UI
// is drawn onto: quads as projected polygons, text by per-run affines.
export class Plane {
  constructor(f, cam, { origin, ex, ey }) {
    this.f = f
    this.cam = cam
    this.origin = origin
    this.ex = norm(ex)
    this.ey = norm(ey)
  }

  at(u, v) { return add(add(this.origin, scale(this.ex, u)), scale(this.ey, v)) }

  poly(depth, pts, { fill, stroke, width = 2, style, composite = 'lighter' }) {
    const q = pts.map(([u, v]) => this.cam.project(this.at(u, v)))
    if (q.some((p) => !p)) return null
    const z = q.reduce((s, p) => s + p.z, 0) / q.length
    const ctx = depth.at(coc(this.f, z, style.focus, style.aperture, 48))
    ctx.save()
    ctx.globalCompositeOperation = composite
    ctx.beginPath()
    q.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)))
    ctx.closePath()
    if (fill) { ctx.fillStyle = fill; ctx.fill() }
    if (stroke) { ctx.lineWidth = width; ctx.strokeStyle = stroke; ctx.lineJoin = 'round'; ctx.stroke() }
    ctx.restore()
    return q
  }

  rect(depth, u0, v0, u1, v1, opts) {
    return this.poly(depth, [[u0, v0], [u1, v0], [u1, v1], [u0, v1]], opts)
  }

  line(depth, u0, v0, u1, v1, color, alpha, { width = 2, style }) {
    return this.poly(depth, [[u0, v0], [u1, v1]], { stroke: rgba(color, alpha), width, style })
  }

  // Text with its baseline at (u, v) and its cap height `h` world units.
  text(depth, str, u, v, h, color, alpha, { style, family = 'Menlo', weight = 400, px = 120, align = 'left' }) {
    const ctx0 = depth.at(0)
    ctx0.font = `${weight} ${px}px "${family}"`
    ctx0.letterSpacing = '0px'
    const w = ctx0.measureText(str).width
    const unit = h / (px * 0.72)
    const u0 = align === 'right' ? u - w * unit : align === 'center' ? u - (w * unit) / 2 : u
    const a = this.cam.project(this.at(u0, v)), b = this.cam.project(this.at(u0 + w * unit, v))
    const t = this.cam.project(this.at(u0, v - h))
    if (!a || !b || !t) return
    const ctx = depth.at(coc(this.f, (a.z + b.z) / 2, style.focus, style.aperture, 48))
    ctx.save()
    ctx.setTransform(ctx.getTransform().multiply(new DOMMatrix([(b.x - a.x) / w, (b.y - a.y) / w, (t.x - a.x) / (-px * 0.72), (t.y - a.y) / (-px * 0.72), a.x, a.y])))
    ctx.font = `${weight} ${px}px "${family}"`
    ctx.letterSpacing = '0px'
    ctx.textBaseline = 'alphabetic'
    ctx.fillStyle = rgba(color, alpha)
    ctx.fillText(str, 0, 0)
    ctx.restore()
    return w * unit
  }
}
