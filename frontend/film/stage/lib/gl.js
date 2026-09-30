export const FULLSCREEN_VS = `#version 300 es
const vec2 P[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
out vec2 vUv;
void main() {
  vec2 p = P[gl_VertexID];
  vUv = p * 0.5 + 0.5;
  gl_Position = vec4(p, 0.0, 1.0);
}`

function compile(gl, type, src, label) {
  const sh = gl.createShader(type)
  gl.shaderSource(sh, src)
  gl.compileShader(sh)
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(sh)
    const lines = src.split('\n').map((l, i) => `${String(i + 1).padStart(4)} ${l}`)
    const at = /ERROR: \d+:(\d+)/.exec(log)
    const near = at ? lines.slice(Math.max(0, +at[1] - 4), +at[1] + 2).join('\n') : ''
    throw new Error(`${label}: shader compile failed\n${log}\n${near}`)
  }
  return sh
}

export class Program {
  constructor(gl, vs, fs, label) {
    this.gl = gl
    this.label = label
    const p = gl.createProgram()
    gl.attachShader(p, compile(gl, gl.VERTEX_SHADER, vs, label))
    gl.attachShader(p, compile(gl, gl.FRAGMENT_SHADER, fs, label))
    gl.linkProgram(p)
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
      throw new Error(`${label}: link failed\n${gl.getProgramInfoLog(p)}`)
    }
    this.p = p
    this.loc = new Map()
    this.types = new Map()
    const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS)
    for (let i = 0; i < n; i++) {
      const info = gl.getActiveUniform(p, i)
      const name = info.name.replace(/\[0\]$/, '')
      this.loc.set(name, gl.getUniformLocation(p, info.name))
      this.types.set(name, { type: info.type, size: info.size })
    }
  }

  use() { this.gl.useProgram(this.p); return this }

  // Textures are passed as { tex } and bound to consecutive units in call order.
  set(uniforms) {
    const gl = this.gl
    let unit = 0
    for (const [name, v] of Object.entries(uniforms)) {
      const loc = this.loc.get(name)
      if (loc == null || v == null) continue
      const { type, size } = this.types.get(name)
      if (v.tex !== undefined) {
        gl.activeTexture(gl.TEXTURE0 + unit)
        gl.bindTexture(gl.TEXTURE_2D, v.tex)
        gl.uniform1i(loc, unit++)
        continue
      }
      const arr = typeof v === 'number' || typeof v === 'boolean' ? [Number(v)] : v
      switch (type) {
        case gl.FLOAT: size > 1 ? gl.uniform1fv(loc, arr) : gl.uniform1f(loc, arr[0]); break
        case gl.FLOAT_VEC2: gl.uniform2fv(loc, arr); break
        case gl.FLOAT_VEC3: gl.uniform3fv(loc, arr); break
        case gl.FLOAT_VEC4: gl.uniform4fv(loc, arr); break
        case gl.INT: case gl.BOOL: size > 1 ? gl.uniform1iv(loc, arr) : gl.uniform1i(loc, arr[0]); break
        case gl.FLOAT_MAT4: gl.uniformMatrix4fv(loc, false, arr); break
        case gl.FLOAT_MAT3: gl.uniformMatrix3fv(loc, false, arr); break
        default: throw new Error(`${this.label}: uniform ${name} has unhandled type ${type}`)
      }
    }
    return this
  }
}

export class Target {
  constructor(gl, w, h, { float = true, filter } = {}) {
    this.gl = gl
    this.w = w
    this.h = h
    this.tex = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, this.tex)
    if (float) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, w, h, 0, gl.RGBA, gl.HALF_FLOAT, null)
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null)
    const f = filter ?? gl.LINEAR
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, f)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, f)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    this.fbo = gl.createFramebuffer()
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo)
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.tex, 0)
    const st = gl.checkFramebufferStatus(gl.FRAMEBUFFER)
    if (st !== gl.FRAMEBUFFER_COMPLETE) throw new Error(`framebuffer ${w}x${h} incomplete: ${st}`)
    gl.bindFramebuffer(gl.FRAMEBUFFER, null)
  }

  bind(clear = true) {
    const gl = this.gl
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo)
    gl.viewport(0, 0, this.w, this.h)
    if (clear) { gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT) }
    return this
  }
}

// A 2D canvas mirrored into a texture once per frame. Flipped on upload so
// canvas pixel (0,0) lands at the top-left of the frame, like the GL passes.
export class CanvasTexture {
  constructor(gl, w, h) {
    this.gl = gl
    this.canvas = new OffscreenCanvas(w, h)
    this.ctx = this.canvas.getContext('2d', { alpha: true })
    this.tex = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, this.tex)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    this.dirty = false
  }

  clear() {
    const c = this.ctx
    c.setTransform(1, 0, 0, 1, 0, 0)
    c.globalAlpha = 1
    c.globalCompositeOperation = 'source-over'
    c.filter = 'none'
    c.shadowBlur = 0
    c.shadowColor = 'transparent'
    c.clearRect(0, 0, this.canvas.width, this.canvas.height)
    this.dirty = false
  }

  upload() {
    const gl = this.gl
    gl.bindTexture(gl.TEXTURE_2D, this.tex)
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, this.canvas)
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false)
  }
}
