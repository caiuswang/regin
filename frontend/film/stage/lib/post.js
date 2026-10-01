import { FULLSCREEN_VS, Program, Target } from './gl.js'
import { COLOR, HEADER, NOISE } from './glsl.js'

const COMPOSITE_FS = HEADER + COLOR + `
uniform sampler2D uScene;
uniform sampler2D uEmit;
uniform sampler2D uOver;
uniform sampler2D uBlurred;
uniform float uEmitGain;
uniform float uOverGain;
uniform float uBar;
uniform vec4 uGlass;
uniform float uGlassRadius;
uniform float uGlassAmt;
uniform vec3 uGlassTint;

vec3 unpremul(vec4 c) { return c.a > 0.0 ? srgb2lin(c.rgb / c.a) * c.a : vec3(0.0); }

float roundedBox(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

void main() {
  vec3 c = texture(uScene, vUv).rgb;
  if (uGlassAmt > 0.0) {
    vec2 px = vUv * uRes;
    vec2 lo = uGlass.xy * uRes, hi = uGlass.zw * uRes;
    float d = roundedBox(px - 0.5 * (lo + hi), 0.5 * (hi - lo), uGlassRadius);
    float inside = 1.0 - smoothstep(-1.5, 1.5, d);
    vec3 frosted = texture(uBlurred, vUv).rgb * uGlassTint;
    c = mix(c, frosted, inside * uGlassAmt);
  }
  c += unpremul(texture(uEmit, vUv)) * uEmitGain;
  vec4 o = texture(uOver, vUv);
  c = c * (1.0 - o.a) + unpremul(o) * uOverGain;
  float m = step(uBar, vUv.y) * step(vUv.y, 1.0 - uBar);
  outColor = vec4(max(c, 0.0) * m, 1.0);
}`

const BRIGHT_FS = HEADER + `
uniform sampler2D uSrc;
uniform float uThreshold;
uniform float uKnee;
void main() {
  vec3 c = min(texture(uSrc, vUv).rgb, vec3(48.0));
  float br = max(c.r, max(c.g, c.b));
  float rq = clamp(br - uThreshold + uKnee, 0.0, 2.0 * uKnee);
  rq = rq * rq / (4.0 * uKnee + 1e-5);
  float w = max(rq, br - uThreshold) / max(br, 1e-5);
  outColor = vec4(c * w, 1.0);
}`

// Jimenez 2014 (CoD: Advanced Warfare) 13-tap downsample / tent upsample.
const DOWN_FS = HEADER + `
uniform sampler2D uSrc;
uniform vec2 uTexel;
vec3 s(vec2 o) { return texture(uSrc, vUv + o * uTexel).rgb; }
void main() {
  vec3 a = s(vec2(-2, 2)), b = s(vec2(0, 2)), c = s(vec2(2, 2));
  vec3 d = s(vec2(-2, 0)), e = s(vec2(0, 0)), f = s(vec2(2, 0));
  vec3 g = s(vec2(-2, -2)), h = s(vec2(0, -2)), i = s(vec2(2, -2));
  vec3 j = s(vec2(-1, 1)), k = s(vec2(1, 1)), l = s(vec2(-1, -1)), m = s(vec2(1, -1));
  vec3 r = e * 0.125 + (a + c + g + i) * 0.03125 + (b + d + f + h) * 0.0625 + (j + k + l + m) * 0.125;
  outColor = vec4(r, 1.0);
}`

const UP_FS = HEADER + `
uniform sampler2D uSrc;
uniform sampler2D uBase;
uniform vec2 uTexel;
uniform float uRadius;
uniform float uBaseWeight;
vec3 s(vec2 o) { return texture(uSrc, vUv + o * uTexel * uRadius).rgb; }
void main() {
  vec3 r = s(vec2(0, 0)) * 4.0
    + (s(vec2(-1, 0)) + s(vec2(1, 0)) + s(vec2(0, -1)) + s(vec2(0, 1))) * 2.0
    + s(vec2(-1, -1)) + s(vec2(1, -1)) + s(vec2(-1, 1)) + s(vec2(1, 1));
  outColor = vec4(texture(uBase, vUv).rgb * uBaseWeight + r / 16.0, 1.0);
}`

const STREAK_FS = HEADER + `
uniform sampler2D uSrc;
uniform vec2 uTexel;
uniform float uStep;
void main() {
  vec3 r = vec3(0.0);
  float wsum = 0.0;
  for (int i = -3; i <= 3; i++) {
    float w = exp(-0.35 * float(i * i));
    r += texture(uSrc, vUv + vec2(float(i) * uStep * uTexel.x, 0.0)).rgb * w;
    wsum += w;
  }
  outColor = vec4(r / wsum, 1.0);
}`

const FINAL_FS = HEADER + NOISE + COLOR + `
uniform sampler2D uComp;
uniform sampler2D uBloom;
uniform sampler2D uStreak;
uniform sampler2D uSubs;
uniform float uFrame;
uniform float uBar;
uniform float uExposure;
uniform float uBloomStr;
uniform float uStreakStr;
uniform vec3 uBloomTint;
uniform vec3 uStreakTint;
uniform float uCA;
uniform float uVignette;
uniform float uGrain;
uniform float uSat;
uniform float uContrast;
uniform vec3 uWB;
uniform vec3 uLift;
uniform vec3 uGamma;
uniform vec3 uGain;
uniform float uFade;

const mat3 ACES_IN = mat3(0.59719, 0.07600, 0.02840, 0.35458, 0.90834, 0.13383, 0.04823, 0.01566, 0.83777);
const mat3 ACES_OUT = mat3(1.60475, -0.10208, -0.00327, -0.53108, 1.10813, -0.07276, -0.07367, -0.00605, 1.07602);
vec3 rrtOdt(vec3 v) {
  vec3 a = v * (v + 0.0245786) - 0.000090537;
  vec3 b = v * (0.983729 * v + 0.4329510) + 0.238081;
  return a / b;
}
vec3 aces(vec3 c) { return clamp(ACES_OUT * rrtOdt(ACES_IN * c), 0.0, 1.0); }

vec3 hdr(vec2 uv) {
  return texture(uComp, uv).rgb
    + texture(uBloom, uv).rgb * uBloomStr * uBloomTint
    + texture(uStreak, uv).rgb * uStreakStr * uStreakTint;
}

void main() {
  vec2 uv = vUv;
  float aspect = uRes.x / uRes.y;
  vec2 d = (uv - 0.5) * vec2(aspect, 1.0);
  float r2 = dot(d, d);
  vec2 off = (uv - 0.5) * uCA * r2;
  vec3 c = vec3(hdr(uv - off).r, hdr(uv).g, hdr(uv + off).b);
  c *= uWB * exp2(uExposure);
  float l = luma(c);
  c = mix(vec3(l), c, uSat);
  c *= 1.0 - uVignette * smoothstep(0.15, 1.1, r2);
  c = aces(c);
  c = lin2srgb(c);
  c = uGain * (c + uLift * (1.0 - c));
  c = pow(max(c, 0.0), 1.0 / uGamma);
  c = clamp(c + (c - 0.5) * (uContrast - 1.0) * smoothstep(0.0, 0.25, c), 0.0, 1.0);
  float n1 = hash12(floor(gl_FragCoord.xy) + fract(uFrame * 0.61803) * vec2(1973.0, 3319.0));
  float n2 = vnoise(gl_FragCoord.xy / 1.8 + uFrame * vec2(37.1, 11.3));
  float g = (n1 * 0.55 + n2 * 0.45) - 0.5;
  float lum = luma(c);
  c += g * uGrain * (0.55 + 0.45 * (1.0 - abs(lum - 0.35) * 1.4));
  c *= uFade;
  c *= step(uBar, uv.y) * step(uv.y, 1.0 - uBar);
  vec4 s = texture(uSubs, uv);
  c = c * (1.0 - s.a) + s.rgb;
  outColor = vec4(clamp(c, 0.0, 1.0), 1.0);
}`

// A dissolve: the outgoing frame pushed in about the picture's centre while
// the incoming one fades up. Zooming in never reaches the outgoing frame's
// bars, and the bars stay black.
const MIX_FS = HEADER + `
uniform sampler2D uA;
uniform sampler2D uB;
uniform float uW;
uniform float uZoom;
uniform float uBar;
void main() {
  vec3 a = texture(uA, 0.5 + (vUv - 0.5) / uZoom).rgb;
  vec3 b = texture(uB, vUv).rgb;
  float m = step(uBar, vUv.y) * step(vUv.y, 1.0 - uBar);
  outColor = vec4(mix(a, b, uW) * m, 1.0);
}`

const SCALE_FS = HEADER + `
uniform sampler2D uSrc;
uniform float uWeight;
void main() { outColor = vec4(texture(uSrc, vUv).rgb * uWeight, 1.0); }`

const BLOOM_LEVELS = 7

export class Post {
  constructor(gl, W, H) {
    this.gl = gl
    this.W = W
    this.H = H
    const P = (fs, label) => new Program(gl, FULLSCREEN_VS, fs, label)
    this.progs = {
      composite: P(COMPOSITE_FS, 'composite'),
      bright: P(BRIGHT_FS, 'bright'),
      down: P(DOWN_FS, 'down'),
      up: P(UP_FS, 'up'),
      streak: P(STREAK_FS, 'streak'),
      final: P(FINAL_FS, 'final'),
      mix: P(MIX_FS, 'mix'),
      scale: P(SCALE_FS, 'scale'),
    }
    this.comp = new Target(gl, W, H)
    this.down = []
    this.up = []
    for (let i = 0; i < BLOOM_LEVELS; i++) {
      const w = Math.max(2, W >> (i + 1)), h = Math.max(2, H >> (i + 1))
      this.down.push(new Target(gl, w, h))
      this.up.push(new Target(gl, w, h))
    }
    this.blurA = new Target(gl, W >> 3, H >> 3)
    this.blurB = new Target(gl, W >> 4, H >> 4)
    this.streakA = new Target(gl, W >> 2, H >> 4)
    this.streakB = new Target(gl, W >> 2, H >> 4)
    this.vao = gl.createVertexArray()
  }

  pass(prog, target, uniforms, { add = false } = {}) {
    const gl = this.gl
    if (target) target.bind(false)
    else { gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.viewport(0, 0, this.W, this.H) }
    if (add) { gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE) } else gl.disable(gl.BLEND)
    prog.use().set({ uRes: [target ? target.w : this.W, target ? target.h : this.H], ...uniforms })
    gl.bindVertexArray(this.vao)
    gl.drawArrays(gl.TRIANGLES, 0, 3)
  }

  // A soft copy of the scene, for frosted-glass panels to show through.
  blurScene(scene) {
    const { down } = this.progs
    this.pass(down, this.blurA, { uSrc: scene, uTexel: [4 / this.W, 4 / this.H] })
    this.pass(down, this.blurB, { uSrc: this.blurA, uTexel: [1 / this.blurA.w, 1 / this.blurA.h] })
    return this.blurB
  }

  mix(a, b, { w, zoom, bar }) {
    this.pass(this.progs.mix, null, { uA: a, uB: b, uW: w, uZoom: zoom, uBar: bar })
  }

  // The scene with its 2D layers, in linear HDR, before any lens or film.
  composite({ scene, emit, over, grade: g, bar, glass }) {
    const blurred = glass ? this.blurScene(scene) : scene
    this.pass(this.progs.composite, this.comp, {
      uScene: scene, uEmit: emit, uOver: over, uBlurred: blurred,
      uEmitGain: g.emitGain, uOverGain: g.overGain, uBar: bar,
      uGlass: glass?.rect ?? [0, 0, 0, 0], uGlassRadius: glass?.radius ?? 0,
      uGlassAmt: glass?.amount ?? 0, uGlassTint: glass?.tint ?? [1, 1, 1],
    })
    return this.comp
  }

  // Motion blur: a frame's sub-sample composites summed at weight 1/n, so
  // bloom, grain and the grade run once on the average.
  accumulate(weight, first) {
    this.acc ??= new Target(this.gl, this.W, this.H)
    this.acc.bind(first)
    this.pass(this.progs.scale, this.acc, { uSrc: this.comp, uWeight: weight }, { add: true })
    return this.acc
  }

  finish({ comp, subs, grade, frame, bar, out = null }) {
    const g = grade
    this.pass(this.progs.bright, this.down[0], { uSrc: comp, uThreshold: g.threshold, uKnee: g.knee })
    for (let i = 1; i < BLOOM_LEVELS; i++) {
      const src = this.down[i - 1]
      this.pass(this.progs.down, this.down[i], { uSrc: src, uTexel: [1 / src.w, 1 / src.h] })
    }
    let prev = this.down[BLOOM_LEVELS - 1]
    for (let i = BLOOM_LEVELS - 2; i >= 0; i--) {
      this.pass(this.progs.up, this.up[i], {
        uSrc: prev, uBase: this.down[i], uTexel: [1 / prev.w, 1 / prev.h], uRadius: g.bloomRadius, uBaseWeight: 1,
      })
      prev = this.up[i]
    }
    this.pass(this.progs.down, this.streakA, { uSrc: this.down[0], uTexel: [1 / this.down[0].w, 1 / this.down[0].h] })
    let a = this.streakA, b = this.streakB
    for (const step of [1, 3, 9, 27, 60]) {
      this.pass(this.progs.streak, b, { uSrc: a, uTexel: [1 / a.w, 1 / a.h], uStep: step })
      ;[a, b] = [b, a]
    }
    this.pass(this.progs.final, out, {
      uComp: comp, uBloom: this.up[0], uStreak: a, uSubs: subs,
      uFrame: frame, uBar: bar, uExposure: g.exposure,
      uBloomStr: g.bloom / BLOOM_LEVELS, uStreakStr: g.streak,
      uBloomTint: g.bloomTint, uStreakTint: g.streakTint,
      uCA: g.ca, uVignette: g.vignette, uGrain: g.grain, uSat: g.saturation, uContrast: g.contrast,
      uWB: g.wb, uLift: g.lift, uGamma: g.gamma, uGain: g.gain, uFade: g.fade,
    })
  }
}
