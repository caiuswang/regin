import { cutOf, fadeAt, partsAt } from './lib/cut.js'
import { hashString } from './lib/math.js'
import { Stage, subtitleAt } from './lib/stage.js'
import { WORLDS } from './worlds/index.js'

const q = new URLSearchParams(location.search)
const W = Number(q.get('w')) || 3840
const H = Number(q.get('h')) || 2160
const stage = new Stage(document.getElementById('gl'), W, H)
let board = null
let cut = null
const starts = new Map()

const FONTS = [
  '300 64px "PingFang SC"', '200 64px "PingFang SC"', '400 64px "PingFang SC"',
  '400 64px "Songti SC"', '300 64px "Songti SC"', '200 64px "Avenir Next"', '400 64px "Menlo"',
]

async function load(storyboard) {
  board = storyboard
  cut = cutOf(board)
  for (const r of cut.shots) {
    if (!WORLDS[r.shot.world]) throw new Error(`shot ${r.shot.id}: no world "${r.shot.world}"`)
    starts.set(r.shot.id, r.start)
  }
  await Promise.all(FONTS.map((f) => document.fonts.load(f, '今晚你敢睡吗regin')))
  return { shots: board.shots.length, runtime: cut.runtime, fps: cut.fps, frames: cut.frames, bar: stage.barPx, active: stage.A }
}

function frameOf(shot, t, frame, out = null) {
  const f = {
    t, dur: shot.dur, shot, p: shot.params ?? {}, seed: hashString(shot.id),
    W, H, A: stage.A, frame, subtitle: subtitleAt(shot.lines, t), fade: fadeAt(shot, t), board,
  }
  stage.render(WORLDS[shot.world], f, out)
  return f.subtitle?.text ?? null
}

// A key frame: one shot at shot-local t.
function render(shotId, t) {
  const shot = board.shots.find((s) => s.id === shotId)
  if (!shot) throw new Error(`no shot ${shotId}`)
  return { subtitle: frameOf(shot, t, Math.round((starts.get(shotId) + t) * cut.fps)) }
}

// Frame n of the film, uploaded as raw RGBA rows (bottom row first) to `url`.
// A Blob body: Chromium hands it to the network stack without the per-chunk
// copies an ArrayBuffer body costs, ~20 ms against ~950 ms for a 4K frame.
// A Blob's bytes live outside V8's heap, so V8 never collects the spent ones
// on its own; the browser's blob store fills at ~2 GB and refuses frame 65.
// Collecting after every upload keeps it at one frame.
let pixels = null
async function frame(n, url) {
  const { parts, w, zoom } = partsAt(cut, n)
  const subs = []
  if (parts.length === 1) subs.push(frameOf(parts[0].shot, parts[0].t, n))
  else {
    parts.forEach((p, i) => subs.push(frameOf(p.shot, p.t, n, stage.frames[i])))
    stage.mix(w, zoom)
  }
  const gl = stage.gl
  pixels ??= new Uint8Array(W * H * 4)
  gl.bindFramebuffer(gl.FRAMEBUFFER, null)
  gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
  if (!globalThis.gc) throw new Error('the film needs Chromium started with --js-flags=--expose-gc')
  const res = await fetch(url, { method: 'POST', body: new Blob([pixels]) })
  if (!res.ok) throw new Error(`frame ${n}: upload refused (${res.status} ${await res.text()})`)
  globalThis.gc()
  return { shots: parts.map((p) => p.shot.id), t: parts.map((p) => p.t), w, subtitle: subs.find(Boolean) ?? null }
}

async function encode(blob) {
  const buf = new Uint8Array(await blob.arrayBuffer())
  let s = ''
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000))
  return btoa(s)
}

async function capture(type = 'image/png', quality = 0.95) {
  return encode(await new Promise((ok) => stage.canvas.toBlob(ok, type, quality)))
}

async function preview(width = 1920, quality = 0.9) {
  const h = Math.round((width * H) / W)
  const c = new OffscreenCanvas(width, h)
  const ctx = c.getContext('2d')
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(stage.canvas, 0, 0, width, h)
  return encode(await c.convertToBlob({ type: 'image/jpeg', quality }))
}

window.film = { load, render, frame, capture, preview, size: { W, H } }
