import { at } from '../lib/anim.js'
import { depthFor, font, glowPath, rgba } from '../lib/draw.js'
import { Camera, lerp, mulberry32 } from '../lib/math.js'
import { drawCardLines } from './card.js'

// The plain: a dark, wet ground to the horizon. Each cold light on it is an
// agent at work; the one warm light is the person who asked.
const SKY_FS = `
uniform vec3 uCamPos;
uniform vec3 uCamFwd;
uniform vec3 uCamRight;
uniform vec3 uCamUp;
uniform float uTanH;
uniform float uDawn;
uniform float uStars;
uniform float uCityGlow;
uniform vec3 uCityColor;
uniform float uFogA;
uniform float uFogB;
uniform float uGloss;
uniform float uMist;
uniform vec3 uSunDir;

vec3 skyCol(vec3 rd) {
  float h = rd.y;
  float hp = max(h, 0.0);
  vec3 night = mix(vec3(0.009, 0.016, 0.03), vec3(0.0015, 0.0025, 0.0065), smoothstep(0.0, 0.38, hp));
  night += uCityColor * uCityGlow * (exp(-hp * 26.0) * 0.9 + exp(-hp * 5.0) * 0.1);
  float sunA = max(dot(normalize(vec2(rd.x, rd.z) + 1e-5), normalize(uSunDir.xz)), 0.0);
  vec3 dawn = mix(vec3(0.7, 0.33, 0.15), vec3(0.24, 0.13, 0.17), smoothstep(0.0, 0.04, hp));
  dawn = mix(dawn, vec3(0.045, 0.06, 0.13), smoothstep(0.02, 0.2, hp));
  dawn = mix(dawn, vec3(0.012, 0.02, 0.055), smoothstep(0.2, 0.8, hp));
  dawn += vec3(0.5, 0.22, 0.1) * pow(sunA, 6.0) * exp(-hp * 12.0);
  dawn += vec3(2.6, 1.1, 0.38) * pow(sunA, 80.0) * exp(-hp * 30.0);
  dawn += vec3(0.2, 0.06, 0.09) * exp(-abs(h - 0.025) * 45.0) * (0.3 + 0.7 * sunA);
  vec3 c = mix(night, dawn, uDawn);
  if (h > 0.0 && uStars > 0.0) {
    vec2 sp = vec2(atan(rd.x, rd.z), asin(clamp(h, -1.0, 1.0))) * 420.0;
    vec2 cell = floor(sp);
    vec2 f = fract(sp) - 0.5;
    if (hash12(cell + uSeed) > 0.982) {
      vec2 off = hash22(cell) - 0.5;
      float d = length(f - off * 0.6);
      float b = pow(hash12(cell + 7.0), 7.0) * 3.5 + 0.12;
      float tw = 0.75 + 0.25 * sin(uTime * (1.0 + hash12(cell + 3.0) * 3.0) + hash12(cell) * 30.0);
      c += vec3(0.78, 0.88, 1.0) * b * tw * exp(-d * d * 70.0) * uStars * smoothstep(0.0, 0.1, h) * (1.0 - uDawn);
    }
  }
  return c;
}

vec3 groundCol(vec3 ro, vec3 rd, float t) {
  vec3 p = ro + rd * t;
  vec2 q = p.xz;
  float n1 = fbm(q * 0.09 + 3.1), n2 = fbm(q * 0.09 + 17.3);
  float n3 = vnoise(q * 1.7);
  vec3 n = normalize(vec3((n1 - 0.5) * 0.06 + (n3 - 0.5) * 0.006, 1.0, (n2 - 0.5) * 0.06));
  vec3 r = reflect(rd, n);
  r.y = abs(r.y);
  float cosT = max(dot(-rd, n), 0.0);
  float fres = 0.025 + 0.975 * pow(1.0 - cosT, 5.0);
  vec3 c = vec3(0.0012, 0.0016, 0.0024) + skyCol(r) * fres * uGloss;
  float m = fbm(q * 0.018 + vec2(uTime * 0.05, uTime * 0.02));
  c += skyCol(normalize(vec3(rd.x, 0.02, rd.z))) * uMist * m * m * exp(-t * 0.0035);
  return c;
}

void main() {
  vec2 ndc = vUv * 2.0 - 1.0;
  float aspect = uRes.x / uRes.y;
  vec3 rd = normalize(uCamFwd + uCamRight * ndc.x * uTanH + uCamUp * ndc.y * uTanH / aspect);
  vec3 ro = uCamPos;
  float t = 6000.0;
  vec3 col;
  if (rd.y < -1e-4) { t = -ro.y / rd.y; col = groundCol(ro, rd, t); }
  else col = skyCol(rd);
  vec3 fogCol = skyCol(normalize(vec3(rd.x, 0.004, rd.z)));
  float ry = abs(rd.y) < 1e-4 ? 1e-4 : rd.y;
  float fogAmt = (uFogA / uFogB) * exp(-ro.y * uFogB) * (1.0 - exp(-t * ry * uFogB)) / ry;
  col = mix(col, fogCol, clamp(1.0 - exp(-fogAmt), 0.0, 1.0));
  outColor = vec4(col, 1.0);
}`

const LIGHT_VS = `#version 300 es
layout(location = 0) in vec4 aA;
layout(location = 1) in vec4 aB;
layout(location = 2) in vec4 aC;
uniform vec3 uCamPos;
uniform vec3 uCamFwd;
uniform vec3 uCamRight;
uniform vec3 uCamUp;
uniform float uFocal;
uniform vec2 uRes;
uniform float uTime;
uniform float uReflect;
uniform float uStretch;
uniform float uReflGain;
uniform float uFog;
uniform float uMinPx;
uniform float uFocus;
uniform float uAperture;
uniform vec2 uWakeOrigin;
uniform float uWakeStart;
uniform float uWakeSpeed;
uniform float uWakeJitter;
uniform float uDimStart;
uniform float uDimSpeed;
uniform float uDimJitter;
uniform float uGain;
uniform float uFlicker;
uniform float uPanel;
out vec2 vQ;
out vec3 vCol;
out float vDisc;
out float vPanel;
out float vSeed;
void main() {
  vec3 p = aA.xyz;
  if (uReflect > 0.5) p.y = -p.y;
  vec3 v = p - uCamPos;
  float z = dot(v, uCamFwd);
  vec2 corner = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1)) * 2.0 - 1.0;
  vCol = vec3(0.0);
  vQ = corner;
  vDisc = 0.0;
  vPanel = uPanel;
  vSeed = aC.x;
  if (z < 0.05) { gl_Position = vec4(-9.0, -9.0, 0.0, 1.0); return; }
  float k = uFocal / z;
  vec2 s = vec2(dot(v, uCamRight), dot(v, uCamUp)) * k;
  float sizePx = aA.w * k;
  float coc = uAperture * abs(1.0 / z - 1.0 / uFocus) * uFocal;
  float r = max(max(sizePx, coc), uMinPx);
  float disc = smoothstep(2.5, 7.0, coc / max(sizePx, uMinPx)) * step(5.0, r) * (1.0 - uPanel);
  float dist = length(aA.xz - uWakeOrigin);
  float wake = uWakeStart + dist / uWakeSpeed + aC.y * uWakeJitter;
  float dim = uDimStart + dist / uDimSpeed + aC.z * uDimJitter;
  float on = smoothstep(wake, wake + 0.45, uTime) * (1.0 - smoothstep(dim, dim + 1.4, uTime));
  float flare = 1.0 + 1.6 * exp(-max(uTime - wake, 0.0) * 2.5) * step(wake, uTime);
  float fl = 1.0 - uFlicker * 0.5 * (1.0 + sin(uTime * (2.0 + aC.x * 7.0) + aC.x * 40.0));
  float energy = (sizePx * sizePx) / (r * r);
  vec3 col = aB.rgb * aB.a * on * flare * fl * exp(-z * uFog) * energy * uGain;
  float ext = mix(3.0, 1.06, disc);
  vec2 e = vec2(r) * ext;
  if (uPanel > 0.5) { e = vec2(r * 1.25, r * 0.8) * 1.6; ext = 1.6; }
  if (uReflect > 0.5) { e.y *= uStretch; col *= uReflGain; }
  vQ = corner * ext;
  vCol = col;
  vDisc = disc;
  gl_Position = vec4((s + corner * e) / (uRes * 0.5), 0.0, 1.0);
}`

const LIGHT_FS = `
in vec2 vQ;
in vec3 vCol;
in float vDisc;
in float vPanel;
in float vSeed;
void main() {
  vec2 q = vQ;
  float d = length(q);
  float a;
  if (vPanel > 0.5) {
    vec2 b = abs(q);
    float box = (1.0 - smoothstep(0.92, 1.0, b.x)) * (1.0 - smoothstep(0.92, 1.0, b.y));
    float rows = 0.55 + 0.45 * step(0.35, fract(q.y * 3.2 + vSeed * 7.0)) * step(0.2, hash12(vec2(floor(q.y * 3.2 + vSeed * 7.0), vSeed * 91.0)));
    float halo = exp(-dot(b, b) * 1.2) * 0.22;
    a = box * rows + halo;
  } else {
    float g = exp(-d * d * 2.2);
    float rim = smoothstep(0.55, 0.93, d);
    float disc = (1.0 - smoothstep(0.93, 1.0, d)) * (0.72 + 0.28 * rim);
    a = mix(g, disc * 0.5, vDisc);
  }
  outColor = vec4(vCol * a, 1.0);
}`

const COOL = [[0.33, 0.8, 1.0], [0.72, 0.92, 1.0], [0.28, 0.52, 1.0]]

function buildLights(seed, o = {}) {
  const rnd = mulberry32(seed)
  const count = o.count ?? 9000
  const [x0, x1] = o.x ?? [-320, 320]
  const [z0, z1] = o.z ?? [10, 900]
  const block = o.block ?? 7
  const districts = Array.from({ length: o.districts ?? 16 }, () => ({
    x: lerp(x0, x1, rnd()), z: lerp(z0 + 40, z1, rnd() ** 0.8), r: 30 + rnd() * 110, w: 0.4 + rnd() * 0.8,
  }))
  const density = (x, z) => {
    let d = o.floor ?? 0.06
    for (const c of districts) d += c.w * Math.exp(-((x - c.x) ** 2 + (z - c.z) ** 2) / (c.r * c.r))
    return Math.min(1, d)
  }
  const data = new Float32Array(count * 12)
  let n = 0
  for (let tries = 0; n < count && tries < count * 40; tries++) {
    let x = lerp(x0, x1, rnd()), z = lerp(z0, z1, rnd() ** 0.9)
    if (rnd() > density(x, z)) continue
    if (rnd() < 0.72) {
      if (rnd() < 0.5) x = Math.round(x / block) * block + (rnd() - 0.5) * 0.5
      else z = Math.round(z / block) * block + (rnd() - 0.5) * 0.5
    }
    const k = rnd()
    const col = k < 0.64 ? COOL[0] : k < 0.9 ? COOL[1] : COOL[2]
    const i = n * 12
    const size = (0.07 + rnd() * 0.09) * (o.panel ? 3.2 : 1)
    data.set([x, (o.panel ? 0.35 : 0.12) + rnd() * 0.3, z, size, ...col, (5 + 26 * rnd() ** 2.2) / (o.panel ? 4 : 1), rnd(), rnd(), rnd(), 0], i)
    n++
  }
  return { data: data.subarray(0, n * 12), count: n }
}

function instanceVao(gl, data) {
  const vao = gl.createVertexArray()
  gl.bindVertexArray(vao)
  const buf = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, buf)
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW)
  for (let a = 0; a < 3; a++) {
    gl.enableVertexAttribArray(a)
    gl.vertexAttribPointer(a, 4, gl.FLOAT, false, 48, a * 16)
    gl.vertexAttribDivisor(a, 1)
  }
  gl.bindVertexArray(null)
  return { vao, buf }
}

export function plainCamera(f, p) {
  const c = p.cam ?? {}
  return new Camera({
    pos: at(c.pos ?? [0, 2, 0], f.t), target: at(c.target ?? [0, 1, 100], f.t),
    fov: at(c.fov ?? 50, f.t), roll: at(c.roll ?? 0, f.t), W: f.W, H: f.H,
  })
}

export function drawPlain(f, stage, p, cam) {
  const sky = stage.fullscreen('plain-sky', SKY_FS)
  const dawn = at(p.dawn ?? 0, f.t)
  stage.drawScene(sky, f, {
    ...cam.uniforms(),
    uDawn: dawn, uStars: at(p.stars ?? 1, f.t), uCityGlow: at(p.cityGlow ?? 0.25, f.t),
    uCityColor: p.cityColor ?? [0.1, 0.24, 0.34], uFogA: p.fogA ?? 0.004, uFogB: p.fogB ?? 0.35,
    uGloss: p.gloss ?? 0.9, uMist: at(p.mist ?? 0.25, f.t), uSunDir: p.sunDir ?? [0.35, 0, 1],
  })
  const lights = p.lights
  const prog = stage.program('plain-lights', LIGHT_VS, LIGHT_FS)
  const focus = at(p.focus ?? 1e5, f.t), aperture = at(p.aperture ?? 0, f.t)
  const common = {
    ...cam.uniforms(), uFog: p.lightFog ?? 0.0016, uMinPx: f.H * 0.0007, uFocus: focus, uAperture: aperture,
    uStretch: p.stretch ?? 7, uReflGain: p.reflGain ?? 0.32,
  }
  if (lights) {
    const key = `plain-lights-${JSON.stringify([lights.seed, lights.count, lights.x, lights.z, lights.block, lights.districts, lights.floor, lights.panel])}`
    const built = stage.memo(key, (gl) => {
      const b = buildLights(lights.seed ?? 1, lights)
      return { vao: instanceVao(gl, b.data).vao, count: b.count }
    })
    const w = lights.wake ?? {}, d = lights.dim ?? {}
    const u = {
      ...common, uWakeOrigin: w.origin ?? [0, 0], uWakeStart: w.start ?? -100, uWakeSpeed: w.speed ?? 1e6,
      uWakeJitter: w.jitter ?? 0, uDimStart: d.start ?? 1e6, uDimSpeed: d.speed ?? 1e6, uDimJitter: d.jitter ?? 0,
      uGain: at(lights.gain ?? 1, f.t), uFlicker: lights.flicker ?? 0.15, uPanel: lights.panel ? 1 : 0,
    }
    stage.drawInstanced(prog, f, built.vao, 4, built.count, { ...u, uReflect: 0 })
    stage.drawInstanced(prog, f, built.vao, 4, built.count, { ...u, uReflect: 1 })
  }
  if (p.human) {
    const h = p.human
    const hv = stage.memo('plain-human', (gl) => ({ ...instanceVao(gl, new Float32Array(12)), data: new Float32Array(12) }))
    const breathe = 1 + (h.breathe ?? 0.08) * Math.sin(f.t * 1.4)
    hv.data.set([...at(h.pos, f.t), h.size ?? 0.18, ...(h.color ?? [1.0, 0.6, 0.26]), at(h.intensity ?? 60, f.t) * breathe, 0.5, 0, 0, 0])
    const gl = stage.gl
    gl.bindBuffer(gl.ARRAY_BUFFER, hv.buf)
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, hv.data)
    const u = {
      ...common, uFocus: h.focus ?? focus, uAperture: h.aperture ?? aperture,
      uWakeOrigin: [0, 0], uWakeStart: -100, uWakeSpeed: 1e6, uWakeJitter: 0, uDimStart: 1e6, uDimSpeed: 1e6,
      uDimJitter: 0, uGain: 1, uFlicker: 0, uPanel: 0, uReflGain: h.reflGain ?? 0.4,
    }
    stage.drawInstanced(prog, f, hv.vao, 4, 1, { ...u, uReflect: 0 })
    stage.drawInstanced(prog, f, hv.vao, 4, 1, { ...u, uReflect: 1 })
  }
  return { dawn }
}


// Time-lapse: light streaks running the streets between the agents, the way
// traffic draws lines across a city photographed over hours.
function drawTrails(f, depth, cam, o) {
  const rnd = mulberry32(o.seed ?? 5)
  const block = o.block ?? 7
  const [x0, x1] = o.x ?? [-160, 160], [z0, z1] = o.z ?? [20, 420]
  for (let i = 0; i < (o.count ?? 400); i++) {
    const alongX = rnd() < 0.5
    const lane = alongX ? Math.round(lerp(z0, z1, rnd() ** 0.8) / block) * block : Math.round(lerp(x0, x1, rnd()) / block) * block
    const [a0, a1] = alongX ? [x0, x1] : [z0, z1]
    const span = a1 - a0
    const speed = (o.speed ?? 30) * (0.5 + rnd()) * (rnd() < 0.5 ? -1 : 1)
    const len = (o.length ?? 14) * (0.4 + rnd())
    const head = a0 + ((((rnd() * span + speed * f.t) % span) + span) % span)
    const pts = []
    for (let k = 0; k <= 12; k++) {
      const s = head - Math.sign(speed) * len * (k / 12)
      const q = cam.project(alongX ? [s, 0.15, lane] : [lane, 0.15, s])
      pts.push(q)
    }
    const bright = (o.alpha ?? 0.5) * (0.4 + 0.6 * rnd())
    for (let k = 0; k < 12; k++) {
      const a = pts[k], b = pts[k + 1]
      if (!a || !b) continue
      const path = new Path2D()
      path.moveTo(a.x, a.y)
      path.lineTo(b.x, b.y)
      const w = Math.max(1.0, 0.09 * (cam.focal / a.z))
      glowPath(depth.at(0), path, o.color ?? [150, 220, 255], w, { alpha: bright * (1 - k / 12) ** 1.6 * Math.exp(-a.z * 0.002), halo: 0.7 })
    }
  }
}

function drawClock(f, stage, o) {
  const m = Math.floor(at(o.minutes, f.t))
  const text = `${o.prefix ?? '凌晨'}  ${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
  const c = stage.over
  const size = f.H * 0.02
  font(c, { family: 'PingFang SC', weight: 300, size, tracking: 0.16 })
  c.textBaseline = 'alphabetic'
  c.textAlign = 'left'
  c.fillStyle = rgba([214, 228, 244], 0.78)
  c.fillText(text, f.W * 0.04, stage.A.y + stage.A.h * 0.1)
}

export default {
  grade: 'night',
  render(f, stage) {
    const p = f.p
    const cam = plainCamera(f, p)
    drawPlain(f, stage, p, cam)
    const depth = depthFor(f.W, f.H)
    if (p.trails) drawTrails(f, depth, cam, p.trails)
    if (p.card) drawCardLines(f, stage, depth, p.card)
    depth.flush(stage.emit)
    if (p.clock) drawClock(f, stage, p.clock)
    return { ...(p.look ?? {}), emitGain: p.card?.gain ?? 1.4 }
  },
}
