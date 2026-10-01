import { smooth } from './math.js'

// The cut: shots laid end to end on the frame grid, and how each one enters
// and leaves. A shot with no `enter` / `exit` is cut. A dissolve is declared
// on the incoming shot and is centred on the cut, so the runtime stays the
// sum of the durations: the outgoing shot plays past its end, the incoming
// one from before its start.
const ENTER = new Set(['fade', 'dissolve'])
const EXIT = new Set(['fade'])

function halfDissolve(shot) {
  return shot?.enter?.type === 'dissolve' ? shot.enter.dur / 2 : 0
}

export function validateTransitions(board) {
  board.shots.forEach((shot, i) => {
    const where = `storyboard: shot "${shot.id}"`
    const { enter, exit } = shot
    const next = board.shots[i + 1]
    if (enter && (!ENTER.has(enter.type) || !(enter.dur > 0))) throw new Error(`${where}: enter must be { type: fade | dissolve, dur > 0 }`)
    if (exit && (!EXIT.has(exit.type) || !(exit.dur > 0))) throw new Error(`${where}: exit must be { type: fade, dur > 0 }`)
    if (enter?.push != null && (enter.type !== 'dissolve' || !(enter.push >= 1))) throw new Error(`${where}: push is a dissolve's zoom into the outgoing picture, ≥ 1`)
    if (enter?.type === 'dissolve') {
      if (i === 0) throw new Error(`${where}: the first shot has nothing to dissolve from`)
      if (board.shots[i - 1].exit) throw new Error(`${where}: dissolves in, but "${board.shots[i - 1].id}" fades out`)
    }
    const head = enter?.type === 'fade' ? enter.dur : halfDissolve(shot)
    const tail = exit ? exit.dur : halfDissolve(next)
    if (head + tail > shot.dur + 1e-9) throw new Error(`${where}: its transitions (${head}s in, ${tail}s out) overlap within ${shot.dur}s`)
    // A subtitle never rides a dissolve: it would fade with the picture.
    const lines = shot.lines ?? []
    if (lines.length && lines[0].in < halfDissolve(shot) - 1e-9) throw new Error(`${where}: line ${lines[0].id} starts inside the dissolve in`)
    if (lines.length && lines.at(-1).out > shot.dur - halfDissolve(next) + 1e-9) throw new Error(`${where}: line ${lines.at(-1).id} ends inside the dissolve out`)
  })
}

export function cutOf(board) {
  const fps = board.fps ?? 30
  let at = 0
  const shots = board.shots.map((shot, i) => {
    const row = { shot, i, start: at, f0: Math.round(at * fps), f1: Math.round((at + shot.dur) * fps) }
    at += shot.dur
    return row
  })
  return { fps, frames: shots.at(-1).f1, runtime: at, shots }
}

// The picture's level under its own fades, at shot-local t.
export function fadeAt(shot, t) {
  let m = 1
  if (shot.enter?.type === 'fade') m *= smooth(0, shot.enter.dur, t)
  if (shot.exit?.type === 'fade') m *= 1 - smooth(shot.dur - shot.exit.dur, shot.dur, t)
  return m
}

// What frame n of the cut shows: one shot, or two inside a dissolve with the
// incoming one at weight w and the outgoing one pushed in by `zoom`, which
// accelerates with w.
export function partsAt(cut, n) {
  const i = cut.shots.findIndex((r) => n >= r.f0 && n < r.f1)
  if (i < 0) throw new Error(`frame ${n} is outside the cut (0–${cut.frames - 1})`)
  const row = cut.shots[i]
  const local = (r) => ({ shot: r.shot, t: (n - r.f0) / cut.fps })
  const blend = (a, b) => {
    const h = Math.round(halfDissolve(b.shot) * cut.fps)
    const w = (n - (b.f0 - h) + 0.5) / (2 * h)
    return { parts: [local(a), local(b)], w, zoom: 1 + ((b.shot.enter.push ?? 1) - 1) * w * w }
  }
  const prev = cut.shots[i - 1], next = cut.shots[i + 1]
  if (prev && n < row.f0 + Math.round(halfDissolve(row.shot) * cut.fps)) return blend(prev, row)
  if (next && n >= row.f1 - Math.round(halfDissolve(next.shot) * cut.fps)) return blend(row, next)
  return { parts: [local(row)], w: 0, zoom: 1 }
}
