import { CanvasTexture, FULLSCREEN_VS, Program, Target } from './gl.js'
import { COLOR, HEADER, NOISE } from './glsl.js'
import { gradeFor } from './grade.js'
import { Post } from './post.js'

export const ASPECT = 2.39
const SUB_FADE_S = 0.3
// The film camera's shutter, as a fraction of the frame interval (180°).
const SHUTTER = 0.5

// Chinese film subtitles carry no end punctuation, and a pause inside a line
// is a wide space; a question keeps its mark.
export function subtitleDisplay(text) {
  return text.replace(/[，。、；：]+$/u, '').replace(/[，、；：]/gu, '　')
}

export function subtitleAt(lines, t) {
  for (const l of lines ?? []) {
    if (t >= l.in && t < l.out) {
      const a = Math.min(1, (t - l.in) / SUB_FADE_S, (l.out - t) / SUB_FADE_S)
      return { id: l.id, text: subtitleDisplay(l.text), alpha: Math.max(0, a) }
    }
  }
  return null
}

export class Stage {
  constructor(canvas, W, H) {
    canvas.width = W
    canvas.height = H
    const gl = canvas.getContext('webgl2', {
      antialias: false, alpha: false, depth: false, preserveDrawingBuffer: true,
      premultipliedAlpha: false, powerPreference: 'high-performance',
    })
    if (!gl) throw new Error('WebGL2 unavailable')
    if (!gl.getExtension('EXT_color_buffer_float')) throw new Error('EXT_color_buffer_float unavailable')
    this.gl = gl
    this.canvas = canvas
    this.W = W
    this.H = H
    this.barPx = Math.round((H - W / ASPECT) / 2)
    this.A = { x: 0, y: this.barPx, w: W, h: H - 2 * this.barPx }
    this.scene = new Target(gl, W, H)
    this.layers = { emit: new CanvasTexture(gl, W, H), over: new CanvasTexture(gl, W, H), subs: new CanvasTexture(gl, W, H) }
    this.post = new Post(gl, W, H)
    this.vao = gl.createVertexArray()
    this.cache = new Map()
    this.glass = null
  }

  get emit() { return this.layers.emit.ctx }
  get over() { return this.layers.over.ctx }

  fullscreen(key, fs) {
    if (!this.cache.has(key)) this.cache.set(key, new Program(this.gl, FULLSCREEN_VS, HEADER + NOISE + COLOR + fs, key))
    return this.cache.get(key)
  }

  program(key, vs, fs) {
    if (!this.cache.has(key)) this.cache.set(key, new Program(this.gl, vs, HEADER.replace('in vec2 vUv;\n', '') + NOISE + COLOR + fs, key))
    return this.cache.get(key)
  }

  memo(key, make) {
    if (!this.cache.has(key)) this.cache.set(key, make(this.gl))
    return this.cache.get(key)
  }

  base(f) {
    return { uRes: [this.W, this.H], uTime: f.t, uSeed: f.seed % 1000, uBar: this.barPx / this.H }
  }

  drawScene(prog, f, uniforms = {}, { blend = null } = {}) {
    const gl = this.gl
    this.scene.bind(false)
    this.blend(blend)
    prog.use().set({ ...this.base(f), ...uniforms })
    gl.bindVertexArray(this.vao)
    gl.drawArrays(gl.TRIANGLES, 0, 3)
  }

  drawInstanced(prog, f, vao, vertsPerInstance, instances, uniforms = {}, { blend = 'add' } = {}) {
    const gl = this.gl
    this.scene.bind(false)
    this.blend(blend)
    prog.use().set({ ...this.base(f), ...uniforms })
    gl.bindVertexArray(vao)
    gl.drawArraysInstanced(gl.TRIANGLE_STRIP, 0, vertsPerInstance, instances)
    gl.bindVertexArray(null)
  }

  blend(mode) {
    const gl = this.gl
    if (!mode) { gl.disable(gl.BLEND); return }
    gl.enable(gl.BLEND)
    if (mode === 'add') gl.blendFunc(gl.ONE, gl.ONE)
    else if (mode === 'over') gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    else throw new Error(`unknown blend ${mode}`)
  }

  drawSubtitle(sub) {
    if (!sub || sub.alpha <= 0) return
    const c = this.layers.subs.ctx
    const size = Math.round(this.H * 0.0335)
    c.font = `300 ${size}px "PingFang SC"`
    c.letterSpacing = `${(size * 0.14).toFixed(1)}px`
    c.textAlign = 'center'
    c.textBaseline = 'middle'
    c.fillStyle = `rgba(236, 234, 229, ${sub.alpha.toFixed(3)})`
    c.fillText(sub.text, this.W / 2 + size * 0.07, this.H - this.barPx / 2)
  }

  composite(world, f) {
    const { emit, over } = this.layers
    this.scene.bind(true)
    emit.clear()
    over.clear()
    this.glass = null
    const overrides = world.render(f, this) ?? {}
    emit.upload()
    over.upload()
    const grade = gradeFor(f.shot.grade ?? world.grade ?? 'night', { ...overrides, ...(f.shot.look ?? {}) })
    return { grade, comp: this.post.composite({ scene: this.scene, emit, over, grade, bar: this.barPx / this.H, glass: this.glass }) }
  }

  // `out` is an offscreen frame for a dissolve to mix; the canvas otherwise.
  // A shot with `motionBlur: n` integrates n instants across the shutter,
  // centred on t; subtitles, fades and grain belong to the frame, not to them.
  render(world, f, out = null) {
    const n = f.shot.motionBlur ?? 1
    let shot
    for (let k = 0; k < n; k++) {
      const dt = n > 1 ? ((k + 0.5) / n - 0.5) * (SHUTTER / (f.board.fps ?? 30)) : 0
      shot = this.composite(world, dt ? { ...f, t: f.t + dt } : f)
      if (n > 1) shot.comp = this.post.accumulate(1 / n, k === 0)
    }
    const subs = this.layers.subs
    subs.clear()
    this.drawSubtitle(f.subtitle)
    subs.upload()
    shot.grade.fade *= f.fade ?? 1
    this.post.finish({ comp: shot.comp, subs, grade: shot.grade, frame: f.frame, bar: this.barPx / this.H, out })
    this.gl.finish()
  }

  get frames() {
    this._frames ??= [new Target(this.gl, this.W, this.H), new Target(this.gl, this.W, this.H)]
    return this._frames
  }

  mix(w, zoom) {
    const [a, b] = this.frames
    this.post.mix(a, b, { w, zoom, bar: this.barPx / this.H })
    this.gl.finish()
  }
}
