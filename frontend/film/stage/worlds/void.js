// Deep space for the abstract shots: a faint vertical gradient, drifting
// dust, and sparse specks, so black never reads as an empty frame.
export const VOID_FS = `
uniform vec3 uTop;
uniform vec3 uBottom;
uniform float uDust;
uniform vec3 uDustColor;
uniform float uSpecks;
void main() {
  vec2 uv = vUv;
  float aspect = uRes.x / uRes.y;
  vec3 c = mix(uBottom, uTop, uv.y);
  float n = fbm(uv * vec2(aspect, 1.0) * 2.6 + vec2(uTime * 0.025, uTime * 0.008) + uSeed);
  c += uDustColor * uDust * n * n * n * 2.0;
  vec2 g = floor(uv * uRes / 3.0);
  float s = hash12(g + uSeed);
  c += vec3(0.55, 0.72, 1.0) * step(0.9988, s) * pow(hash12(g + 1.7), 3.0) * uSpecks;
  outColor = vec4(c, 1.0);
}`

export function drawVoid(f, stage, o = {}) {
  stage.drawScene(stage.fullscreen('void', VOID_FS), f, {
    uTop: o.top ?? [0.004, 0.007, 0.014], uBottom: o.bottom ?? [0.001, 0.002, 0.004],
    uDust: o.dust ?? 0.05, uDustColor: o.dustColor ?? [0.12, 0.2, 0.32], uSpecks: o.specks ?? 1.2,
  })
}
