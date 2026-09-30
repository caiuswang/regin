import { hashString } from './lib/math.js'
import { Stage, subtitleAt } from './lib/stage.js'
import { WORLDS } from './worlds/index.js'

const q = new URLSearchParams(location.search)
const W = Number(q.get('w')) || 3840
const H = Number(q.get('h')) || 2160
const stage = new Stage(document.getElementById('gl'), W, H)
let board = null
const starts = new Map()

const FONTS = [
  '300 64px "PingFang SC"', '200 64px "PingFang SC"', '400 64px "PingFang SC"',
  '400 64px "Songti SC"', '300 64px "Songti SC"', '200 64px "Avenir Next"', '400 64px "Menlo"',
]

async function load(storyboard) {
  board = storyboard
  let at = 0
  for (const s of board.shots) {
    if (!WORLDS[s.world]) throw new Error(`shot ${s.id}: no world "${s.world}"`)
    starts.set(s.id, at)
    at += s.dur
  }
  await Promise.all(FONTS.map((f) => document.fonts.load(f, '今晚你敢睡吗regin')))
  return { shots: board.shots.length, runtime: at, bar: stage.barPx, active: stage.A }
}

function render(shotId, t) {
  const shot = board.shots.find((s) => s.id === shotId)
  if (!shot) throw new Error(`no shot ${shotId}`)
  const start = starts.get(shotId)
  const seed = hashString(shot.id)
  const f = {
    t, dur: shot.dur, shot, p: shot.params ?? {}, seed,
    W, H, A: stage.A, frame: Math.round((start + t) * (board.fps ?? 30)),
    subtitle: subtitleAt(shot.lines, t), board,
  }
  stage.render(WORLDS[shot.world], f)
  return { subtitle: f.subtitle?.text ?? null }
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

window.film = { load, render, capture, preview, size: { W, H } }
