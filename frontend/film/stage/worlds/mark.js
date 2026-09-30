import { depthFor, font, glowPath, rgba, textCentered } from '../lib/draw.js'
import { clamp, easeOut, lerp, smooth } from '../lib/math.js'

// The studio ident at the top and the logo lockup at the end: a hairline of
// light, the wordmark, and one glint across it.
const FS = `
uniform float uWarm;
uniform float uHorizonY;
void main() {
  vec2 uv = vUv;
  float aspect = uRes.x / uRes.y;
  float n = fbm(vec2(uv.x * aspect, uv.y) * 2.0 + vec2(uTime * 0.03, 0.0) + uSeed);
  vec3 c = vec3(0.012, 0.016, 0.026) * (0.4 + n * n) * (1.0 - smoothstep(0.1, 0.9, length((uv - 0.5) * vec2(1.0, 1.7))));
  float dy = abs(uv.y - uHorizonY) * uRes.y;
  float spread = 1.0 - smoothstep(0.0, 0.6, abs(uv.x - 0.5));
  c += vec3(1.0, 0.56, 0.26) * uWarm * spread * (exp(-dy / 90.0) * 0.12 + exp(-dy / 500.0) * 0.05);
  outColor = vec4(c, 1.0);
}`

const WHITE = [242, 242, 246]
const WARM = [255, 214, 170]

function glint(f, ctx, text, x, y, size, tracking, u) {
  if (u <= 0 || u >= 1) return
  const tmp = new OffscreenCanvas(f.W, Math.ceil(size * 2))
  const t = tmp.getContext('2d')
  font(t, { family: 'Avenir Next', weight: 200, size, tracking })
  t.fillStyle = '#fff'
  textCentered(t, text, x, size, { size, tracking })
  t.globalCompositeOperation = 'source-in'
  const gx = lerp(x - f.W * 0.25, x + f.W * 0.25, u)
  const g = t.createLinearGradient(gx - size, 0, gx + size, size * 2)
  g.addColorStop(0, 'rgba(255,255,255,0)')
  g.addColorStop(0.5, 'rgba(255,255,255,0.95)')
  g.addColorStop(1, 'rgba(255,255,255,0)')
  t.fillStyle = g
  t.fillRect(0, 0, f.W, size * 2)
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  ctx.drawImage(tmp, 0, y - size)
  ctx.restore()
}

function hairline(f, ctx, cx, y, half, alpha) {
  const path = new Path2D()
  path.moveTo(cx - half, y)
  path.lineTo(cx + half, y)
  glowPath(ctx, path, WARM, f.H * 0.0009, { alpha, halo: 1.4 })
}

function ident(f, stage, p, depth) {
  const { A } = stage
  const cx = f.W / 2
  const line = easeOut(clamp((f.t - 0.2) / 1.3))
  hairline(f, depth.at(0), cx, A.y + A.h * 0.585, f.W * 0.2 * line, 0.8 * (1 - smooth(3.2, 4.6, f.t) * 0.6))
  const word = smooth(0.9, 2.1, f.t)
  const size = f.H * 0.1, tracking = lerp(0.6, 0.46, easeOut(clamp((f.t - 0.9) / 3)))
  const ctx = depth.at((1 - word) * 18)
  font(ctx, { family: 'Avenir Next', weight: 200, size, tracking })
  ctx.fillStyle = rgba(WHITE, word)
  textCentered(ctx, p.word ?? 'regin', cx, A.y + A.h * 0.47, { size, tracking })
  glint(f, depth.at(0), p.word ?? 'regin', cx, A.y + A.h * 0.47, size, tracking, clamp((f.t - 1.9) / 1.1))
  const sub = smooth(2.0, 2.9, f.t)
  const ss = f.H * 0.021
  const sctx = depth.at(0)
  font(sctx, { family: 'PingFang SC', weight: 300, size: ss, tracking: 1.1 })
  sctx.fillStyle = rgba(WARM, 0.8 * sub)
  textCentered(sctx, p.sub ?? '出品', cx, A.y + A.h * 0.66, { size: ss, tracking: 1.1 })
}

function logo(f, stage, p, depth) {
  const { A } = stage
  const cx = f.W / 2
  const m = f.H * 0.085, my = A.y + A.h * 0.3
  const draw = easeOut(clamp((f.t - 0.3) / 1.2))
  const box = new Path2D()
  box.roundRect(cx - m / 2, my - m / 2, m, m, m * 0.26)
  const ctx = depth.at(0)
  ctx.save()
  ctx.setLineDash([m * 4 * draw, m * 4])
  glowPath(ctx, box, WARM, f.H * 0.0014, { alpha: 0.95 })
  ctx.restore()
  const r = smooth(0.9, 1.6, f.t)
  font(ctx, { family: 'Avenir Next', weight: 400, size: m * 0.56, tracking: 0 })
  ctx.fillStyle = rgba(WHITE, r)
  textCentered(ctx, 'r', cx, my + m * 0.02, { size: m * 0.56, tracking: 0 })
  const word = smooth(1.2, 2.2, f.t)
  const size = f.H * 0.078, tracking = 0.36
  const wctx = depth.at((1 - word) * 16)
  font(wctx, { family: 'Avenir Next', weight: 200, size, tracking })
  wctx.fillStyle = rgba(WHITE, word)
  textCentered(wctx, 'regin', cx, A.y + A.h * 0.53, { size, tracking })
  glint(f, depth.at(0), 'regin', cx, A.y + A.h * 0.53, size, tracking, clamp((f.t - 2.4) / 1.1))
  const sub = smooth(1.9, 2.8, f.t)
  const ss = f.H * 0.026
  const sctx = depth.at(0)
  font(sctx, { family: 'PingFang SC', weight: 300, size: ss, tracking: 0.62 })
  sctx.fillStyle = rgba(WARM, 0.92 * sub)
  textCentered(sctx, p.sub ?? '未来 AI 工作台', cx, A.y + A.h * 0.66, { size: ss, tracking: 0.62 })
}

export default {
  grade: 'card',
  render(f, stage) {
    const p = f.p
    stage.drawScene(stage.fullscreen('mark', FS), f, { uWarm: p.warm ?? 0, uHorizonY: p.horizonY ?? 0.42 })
    const depth = depthFor(f.W, f.H)
    if (p.mode === 'ident') ident(f, stage, p, depth)
    else if (p.mode === 'logo') logo(f, stage, p, depth)
    else throw new Error(`mark: unknown mode ${p.mode}`)
    depth.flush(stage.emit)
    const out = p.out != null ? 1 - smooth(p.out, f.dur, f.t) : 1
    return { emitGain: p.gain ?? 1.5, fade: out }
  },
}
