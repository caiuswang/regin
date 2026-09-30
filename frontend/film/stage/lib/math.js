export const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x))
export const lerp = (a, b, t) => a + (b - a) * t
export const mix3 = (a, b, t) => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)]
export const smooth = (e0, e1, x) => { const t = clamp((x - e0) / (e1 - e0)); return t * t * (3 - 2 * t) }
export const easeInOut = (t) => { t = clamp(t); return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2 }
export const easeOut = (t) => 1 - (1 - clamp(t)) ** 3
export const easeIn = (t) => clamp(t) ** 3
// Slow-in slow-out over [t0, t1], held at the ends: the camera curve every
// move uses, so a cut can land on any frame without a jolt.
export const ramp = (t, t0, t1, f = easeInOut) => f(clamp((t - t0) / (t1 - t0)))

export const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
export const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
export const scale = (a, k) => [a[0] * k, a[1] * k, a[2] * k]
export const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
export const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
export const norm = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l] }

export function mulberry32(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function hashString(s) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return h >>> 0
}

// Horizontal field of view: the frame is composed for its width, and the
// letterbox only masks rows, so a shot keeps its framing at any bar height.
// Looking down +z, +x is screen-right (a left-handed view), so storyboard
// coordinates read the way the frame does.
export class Camera {
  constructor({ pos, target, fov = 50, W, H, roll = 0 }) {
    this.pos = pos
    this.W = W
    this.H = H
    this.tanH = Math.tan((fov * Math.PI) / 360)
    this.focal = W / 2 / this.tanH
    this.fwd = norm(sub(target, pos))
    let right = norm(cross([0, 1, 0], this.fwd))
    let up = cross(this.fwd, right)
    if (roll) {
      const c = Math.cos(roll), s = Math.sin(roll)
      ;[right, up] = [add(scale(right, c), scale(up, s)), add(scale(up, c), scale(right, -s))]
    }
    this.right = right
    this.up = up
  }

  project(p) {
    const v = sub(p, this.pos)
    const z = dot(v, this.fwd)
    if (z <= 1e-3) return null
    const k = this.focal / z
    return { x: this.W / 2 + dot(v, this.right) * k, y: this.H / 2 - dot(v, this.up) * k, z, k }
  }

  uniforms() {
    return {
      uCamPos: this.pos, uCamFwd: this.fwd, uCamRight: this.right, uCamUp: this.up,
      uTanH: this.tanH, uFocal: this.focal,
    }
  }
}
