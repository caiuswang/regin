import { at } from '../lib/anim.js'
import { depthFor, rgba } from '../lib/draw.js'
import { drawGlyph, glyphFrame, layoutLine } from '../lib/glyphs.js'
import { Camera, clamp, mulberry32, norm, smooth } from '../lib/math.js'

// Macro on a dark glass desk: out-of-focus lights behind, mirrored below the
// glass line, softer and dimmer than the lights themselves.
const FS = `
uniform float uHorizon;
uniform float uWarmth;
uniform float uGlow;

vec3 field(vec2 uv, float aspect, float soft, float grow) {
  vec3 c = vec3(0.0);
  for (int i = 0; i < 44; i++) {
    float fi = float(i);
    vec2 h = hash22(vec2(fi * 1.37, uSeed + 5.0));
    vec2 pos = vec2(h.x * 1.2 - 0.1, uHorizon + 0.02 + pow(h.y, 1.4) * 0.55);
    pos.x += sin(uTime * 0.12 + fi) * 0.004;
    float r = (0.012 + 0.045 * pow(hash12(vec2(fi, 3.1)), 1.6)) * grow;
    vec2 d = (uv - pos) * vec2(aspect, 1.0);
    float dist = length(d) / r;
    float disc = 1.0 - smoothstep(1.0 - 0.07 * soft, 1.0, dist);
    float rim = smoothstep(0.6, 0.97, dist) * 0.35 + 0.65;
    float warm = step(0.72, hash12(vec2(fi, 9.7)));
    vec3 col = mix(vec3(0.18, 0.55, 0.9), vec3(1.0, 0.55, 0.22), warm * uWarmth);
    float b = 0.05 + 0.2 * pow(hash12(vec2(fi, 1.9)), 3.0);
    c += col * disc * rim * b;
  }
  return c;
}

void main() {
  vec2 uv = vUv;
  float aspect = uRes.x / uRes.y;
  vec3 c = vec3(0.0);
  float edge = smoothstep(-0.012, 0.012, uv.y - uHorizon);
  if (uv.y > uHorizon - 0.012) {
    c = field(uv, aspect, 1.0, 1.0) * edge;
    c += vec3(0.02, 0.03, 0.05) * exp(-(uv.y - uHorizon) * 9.0) * edge;
  }
  if (uv.y < uHorizon + 0.012) {
    vec3 c2;
    vec2 m = vec2(uv.x, 2.0 * uHorizon - uv.y);
    float depth = max(uHorizon - uv.y, 0.0);
    c2 = field(m + vec2(0.0, sin(uv.x * 60.0 + uTime) * 0.0015), aspect, 6.0, 1.15) * 0.32 * exp(-depth * 3.0);
    c2 += vec3(0.03, 0.045, 0.07) * exp(-depth * 40.0) * 0.8;
    c2 += vec3(1.0, 0.6, 0.3) * uGlow * exp(-depth * 18.0) * 0.05;
    c = mix(c2, c, edge);
  }
  outColor = vec4(c, 1.0);
}`

const WARM = [255, 206, 150]

function typedTimes(glyphCount, type) {
  const rnd = mulberry32(17)
  let at0 = type.start ?? 0.8
  const times = []
  for (let i = 0; i < glyphCount; i++) {
    times.push(at0)
    at0 += (1 / (type.cps ?? 5)) * (0.55 + rnd() * 0.9) + (/[，。]/.test(type.text[i]) ? 0.35 : 0)
  }
  return times
}

export default {
  grade: 'warm',
  render(f, stage) {
    const p = f.p
    const cam = new Camera({
      pos: at(p.cam?.pos ?? [-0.6, 0.62, -4.4], f.t), target: at(p.cam?.target ?? [0.8, 0.28, 0.9], f.t),
      fov: at(p.cam?.fov ?? 34, f.t), W: f.W, H: f.H,
    })
    const glow = clamp(f.t / 2)
    const flat = norm([cam.fwd[0], 0, cam.fwd[2]])
    const far = cam.project([cam.pos[0] + flat[0] * 1e5, cam.pos[1], cam.pos[2] + flat[2] * 1e5])
    const horizon = 1 - far.y / f.H
    stage.drawScene(stage.fullscreen('sentence', FS), f, { uHorizon: horizon, uWarmth: 1, uGlow: glow })

    const text = p.text
    const depth = depthFor(f.W, f.H)
    const layout = layoutLine(depth.at(0), text, { family: p.family ?? 'Songti SC', weight: 400, px: 200, tracking: 0.08 })
    const unit = (p.glyphHeight ?? 0.42) / 200 / 0.8
    const width = layout.width * unit
    const dir = norm(p.dir ?? [0.88, 0, 0.47])
    const origin = [-(width / 2) * dir[0] + (p.shift ?? 0), 0, -(width / 2) * dir[2]]
    const times = typedTimes(layout.glyphs.length, { ...(p.type ?? {}), text: [...text] })
    const focusZ = at(p.focus ?? 4.6, f.t)
    const aperture = p.aperture ?? 1.3
    const disperse = p.disperse ? clamp((f.t - p.disperse.start) / p.disperse.dur) : 0
    const rnd = mulberry32(99)
    let lastFrame = null
    layout.glyphs.forEach((g, i) => {
      const born = f.t - times[i]
      const jitter = [rnd(), rnd(), rnd()]
      if (born < 0) return
      const fr = glyphFrame(cam, origin, dir, unit, g, layout.px * 0.8 * unit)
      if (!fr) return
      lastFrame = { fr, g }
      const flash = 1 + 1.4 * Math.exp(-born * 6)
      const on = smooth(0, 0.09, born) * (1 - smooth(0.1 + jitter[0] * 0.6, 0.7 + jitter[0] * 0.6, disperse))
      const blur = Math.min(40, aperture * Math.abs(1 / fr.z - 1 / focusZ) * f.H * 0.9)
      const ctx = depth.at(blur)
      drawGlyph(ctx, g, fr, layout, WARM, on * flash * 0.95)
      drawGlyph(depth.at(blur + 5), g, fr, layout, WARM, on * 0.22, { mirror: true })
    })
    const typingDone = f.t > times.at(-1) + 0.2
    const blinkOn = !typingDone || Math.floor((f.t - times.at(-1)) / 0.53) % 2 === 0
    if (lastFrame && blinkOn && disperse === 0) {
      const { fr } = lastFrame
      const ctx = depth.at(Math.min(40, aperture * Math.abs(1 / fr.z - 1 / focusZ) * f.H * 0.9))
      const x = fr.pb.x + (fr.pb.x - fr.pa.x) * 0.12
      const h = fr.pa.y - fr.pt.y
      ctx.fillStyle = rgba([255, 190, 120], 0.95)
      ctx.fillRect(x, fr.pb.y - h * 1.02, Math.max(3, h * 0.045), h * 1.12)
    }
    depth.flush(stage.emit)
    return { emitGain: p.gain ?? 1.7 }
  },
}
