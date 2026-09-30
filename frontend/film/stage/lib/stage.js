import { CanvasTexture, FULLSCREEN_VS, Program, Target } from './gl.js'
import { COLOR, HEADER, NOISE } from './glsl.js'
import { gradeFor } from './grade.js'
import { Post } from './post.js'

export const ASPECT = 2.39
const SUB_FADE_S = 0.3

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

  render(world, f) {
    const gl = this.gl
    this.scene.bind(true)
    for (const l of Object.values(this.layers)) l.clear()
    this.glass = null
    const overrides = world.render(f, this) ?? {}
    this.drawSubtitle(f.subtitle)
    for (const l of Object.values(this.layers)) l.upload()
    const grade = gradeFor(f.shot.grade ?? world.grade ?? 'night', { ...overrides, ...(f.shot.look ?? {}) })
    this.post.run({
      scene: this.scene, emit: this.layers.emit, over: this.layers.over, subs: this.layers.subs,
      grade, frame: f.frame, bar: this.barPx / this.H, glass: this.glass,
    })
    gl.finish()
  }
}
