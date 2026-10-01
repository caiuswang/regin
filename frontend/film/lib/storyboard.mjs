import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { validateTransitions } from '../stage/lib/cut.js'

const STAGE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'stage')

export function loadStoryboard(path) {
  const board = JSON.parse(readFileSync(path, 'utf8'))
  if (!board.shots?.length) throw new Error('storyboard: no shots')
  const ids = new Set()
  const acts = new Set((board.acts ?? []).map((a) => a.id))
  for (const shot of board.shots) {
    const where = `storyboard: shot "${shot.id}"`
    for (const id of [shot.id, ...(shot.lines ?? []).map((l) => l.id)]) {
      if (!id) throw new Error(`${where}: missing id`)
      if (ids.has(id)) throw new Error(`storyboard: duplicate id "${id}"`)
      ids.add(id)
    }
    if (!shot.world) throw new Error(`${where}: no world`)
    if (!(shot.dur > 0)) throw new Error(`${where}: dur must be > 0`)
    if (!shot.picture?.trim()) throw new Error(`${where}: no picture (画面) description`)
    if (!acts.has(shot.act)) throw new Error(`${where}: act "${shot.act}" is not declared in acts`)
    if (shot.motionBlur != null && !(Number.isInteger(shot.motionBlur) && shot.motionBlur >= 1)) throw new Error(`${where}: motionBlur is a sample count, an integer ≥ 1`)
    if (Math.abs(shot.dur * (board.fps ?? 30) - Math.round(shot.dur * (board.fps ?? 30))) > 1e-6) throw new Error(`${where}: dur ${shot.dur}s is not a whole number of frames`)
    let last = 0
    for (const l of shot.lines ?? []) {
      if (!l.text?.trim()) throw new Error(`${where}: line ${l.id} has no text`)
      if (!(l.in >= last && l.out > l.in && l.out <= shot.dur)) {
        throw new Error(`${where}: line ${l.id} [${l.in}, ${l.out}] must start after the previous line and end within the shot (${shot.dur}s)`)
      }
      last = l.out
    }
  }
  validateTransitions(board)
  return board
}

// One key frame per subtitle line, taken 60% into the line, so each line is
// reviewed over the picture it plays on; a shot without lines shows its hero.
// Times snap to the frame grid, so every key frame is a frame of the film.
export function stillsOf(shot, fps) {
  const snap = (t) => Math.round(t * fps) / fps
  if (shot.stills?.length) return shot.stills.map((t, i) => ({ tag: `${shot.id}-${i + 1}`, t: snap(t) }))
  if (shot.lines?.length) return shot.lines.map((l) => ({ tag: l.id, t: snap(l.still ?? l.in + (l.out - l.in) * 0.6) }))
  return [{ tag: shot.id, t: snap(shot.hero ?? shot.dur * 0.6) }]
}

export function timeline(board) {
  let at = 0
  return board.shots.map((s) => {
    const row = { shot: s, start: at, end: at + s.dur }
    at += s.dur
    return row
  })
}

// What a rendered film was made from: the storyboard and every stage source.
// A film whose digest differs from the current one is stale.
export function sourceDigest(sbPath) {
  const h = createHash('sha256').update(readFileSync(sbPath))
  const files = readdirSync(STAGE_DIR, { recursive: true }).filter((f) => /\.(js|html)$/.test(f)).sort()
  for (const f of files) h.update(f).update(readFileSync(join(STAGE_DIR, f)))
  return h.digest('hex').slice(0, 16)
}

// The film's file name: before-dawn.2160p.mp4.
export const slugOf = (board) => board.title_en.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

export const tc = (s) => {
  const m = Math.floor(s / 60)
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, '0')}`
}
