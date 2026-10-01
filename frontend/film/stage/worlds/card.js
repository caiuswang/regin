import { depthFor, dot, font, rgba, textCentered } from '../lib/draw.js'
import { clamp, easeOut, mulberry32, ramp } from '../lib/math.js'
import { drawWeave } from './weave.js'

const FS = `
uniform float uBar;
uniform float uHaze;
uniform vec3 uHazeColor;
uniform float uHorizon;
uniform vec3 uHorizonColor;
uniform float uHorizonY;
void main() {
  vec2 uv = vUv;
  float aspect = uRes.x / uRes.y;
  float n = fbm(vec2(uv.x * aspect, uv.y) * 2.2 + vec2(uTime * 0.035, uTime * 0.01) + uSeed);
  float fall = 0.35 + 0.65 * (1.0 - smoothstep(0.0, 1.25, length((uv - 0.5) * vec2(0.8, 1.3))));
  vec3 c = uHazeColor * uHaze * (0.25 + n * n * 1.6) * fall;
  float dy = abs(uv.y - uHorizonY) * uRes.y;
  float spread = 1.0 - smoothstep(0.0, 0.62, abs(uv.x - 0.5));
  c += uHorizonColor * uHorizon * spread * (exp(-dy / 3.0) * 1.4 + exp(-dy / 55.0) * 0.28 + exp(-dy / 380.0) * 0.07);
  outColor = vec4(c, 1.0);
}`

const WARM_WHITE = [255, 244, 228]

// Out-of-focus motes drifting through the dark: depth without a subject.
function motes(f, stage, depth, { count = 70, color = [255, 214, 170], strength = 1 }) {
  const rnd = mulberry32(f.seed ^ 0x9e3779b9)
  const { A } = stage
  for (let i = 0; i < count; i++) {
    const x0 = rnd() * f.W, y0 = A.y + rnd() * A.h
    const z = rnd()
    const drift = (8 + 30 * z) * f.t
    const x = x0 + drift * (rnd() - 0.3), y = y0 - drift * 0.35
    const blur = z < 0.7 ? 6 + 60 * (0.7 - z) : 1.5
    const r = f.H * (0.0012 + 0.004 * (1 - z))
    const a = strength * (0.18 + 0.5 * rnd()) * (z < 0.7 ? 0.5 : 1)
    dot(depth.at(blur), x, y, r * (blur > 10 ? 3 : 1.6), color, a)
  }
}

// Title-card type: each line resolves out of focus into place, and the whole
// card keeps a slow push-in and a slowly widening letter-spacing.
export function drawCardLines(f, stage, depth, p) {
  const fadeIn = p.in ?? 0.35
  const push = 1 + (p.push ?? 0.03) * (f.t / f.dur)
  const cx = f.W / 2, cy = stage.A.y + stage.A.h * (p.y ?? 0.5)
  for (const [i, line] of (p.lines ?? []).entries()) {
    const delay = line.delay ?? i * 0.5
    const r = easeOut(clamp((f.t - fadeIn - delay) / (p.reveal ?? 1.2)))
    if (r <= 0) continue
    const size = f.H * (line.size ?? 0.085) * push
    const tracking = (line.tracking ?? 0.3) + (p.spread ?? 0.04) * (f.t / f.dur)
    const ctx = depth.at((1 - r) * (line.blurFrom ?? 26))
    font(ctx, { family: line.family ?? 'Songti SC', weight: line.weight ?? 400, size, tracking })
    ctx.fillStyle = rgba(line.color ?? WARM_WHITE, r * (line.alpha ?? 1))
    const y = cy + (line.dy ?? 0) * f.H * push + (1 - r) * f.H * 0.008
    textCentered(ctx, line.text, cx, y, { size, tracking })
  }
}

export default {
  grade: 'card',
  render(f, stage) {
    const p = f.p
    const prog = stage.fullscreen('card', FS)
    stage.drawScene(prog, f, {
      uHaze: p.haze ?? 0.018,
      uHazeColor: p.hazeColor ?? [0.35, 0.45, 0.6],
      uHorizon: (p.horizon ?? 0) * ramp(f.t, 0, 2.5),
      uHorizonColor: p.horizonColor ?? [1.0, 0.55, 0.22],
      uHorizonY: p.horizonY ?? 0.36,
    })
    const depth = depthFor(f.W, f.H)
    if (p.motes) motes(f, stage, depth, p.motes)

    if (p.tangle) drawWeave(f, stage, depth, { beam: -1, alpha: p.tangle.alpha ?? 0.5, blurBoost: p.tangle.blur ?? 10, speed: p.tangle.speed ?? 0.4 })
    drawCardLines(f, stage, depth, p)
    depth.flush(stage.emit)
    return { emitGain: p.gain ?? 1.5 }
  },
}
