import { at } from './anim.js'
import { dot, glowPath, rgba } from './draw.js'
import { Camera, add, lerp, scale } from './math.js'

export function cameraFrom(f, c = {}) {
  return new Camera({
    pos: at(c.pos ?? [0, 0, -8], f.t), target: at(c.target ?? [0, 0, 0], f.t),
    fov: at(c.fov ?? 45, f.t), roll: at(c.roll ?? 0, f.t), W: f.W, H: f.H,
  })
}

// Thin-lens circle of confusion in output pixels, capped so a near element
// stays a soft shape rather than a smear across the frame.
export function coc(f, z, focus, aperture, cap = 64) {
  return Math.min(cap, aperture * Math.abs(1 / z - 1 / focus) * f.H)
}

export function bezier(a, c, b) {
  return (u) => {
    const v = 1 - u
    return [v * v * a[0] + 2 * v * u * c[0] + u * u * b[0], v * v * a[1] + 2 * v * u * c[1] + u * u * b[1], v * v * a[2] + 2 * v * u * c[2] + u * u * b[2]]
  }
}

export function arc(a, b, bend) {
  const mid = scale(add(a, b), 0.5)
  return bezier(a, add(mid, bend), b)
}

export function strokeCurve(f, depth, cam, fn, u0, u1, { color, width = 0.02, alpha = 1, focus = 8, aperture = 0, halo = 1, segs = 28, minPx = 1.4 }) {
  if (u1 <= u0) return
  const pts = []
  let zs = 0
  for (let i = 0; i <= segs; i++) {
    const q = cam.project(fn(lerp(u0, u1, i / segs)))
    if (!q) continue
    pts.push(q)
    zs += q.z
  }
  if (pts.length < 2) return
  const z = zs / pts.length
  const px = Math.max(minPx, width * (cam.focal / z))
  const energy = Math.min(1, (width * (cam.focal / z)) / minPx)
  const path = new Path2D()
  pts.forEach((q, i) => (i ? path.lineTo(q.x, q.y) : path.moveTo(q.x, q.y)))
  glowPath(depth.at(coc(f, z, focus, aperture)), path, color, px, { alpha: alpha * Math.max(0.35, energy), halo })
}

export function node(f, depth, cam, p, { r = 0.06, color, alpha = 1, focus = 8, aperture = 0, halo = 3 }) {
  const q = cam.project(p)
  if (!q) return null
  const px = Math.max(1.5, r * q.k)
  const ctx = depth.at(coc(f, q.z, focus, aperture))
  dot(ctx, q.x, q.y, px * halo, color, alpha * 0.35)
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  ctx.fillStyle = rgba(color, alpha)
  ctx.beginPath()
  ctx.arc(q.x, q.y, px, 0, Math.PI * 2)
  ctx.fill()
  ctx.restore()
  return q
}

export function ring(f, depth, cam, p, { r, color, alpha = 1, width = 3, focus = 8, aperture = 0 }) {
  const q = cam.project(p)
  if (!q) return
  const ctx = depth.at(coc(f, q.z, focus, aperture))
  const path = new Path2D()
  path.arc(q.x, q.y, r * q.k, 0, Math.PI * 2)
  glowPath(ctx, path, color, width, { alpha })
}
