import { readFileSync } from 'node:fs'

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
    let last = 0
    for (const l of shot.lines ?? []) {
      if (!l.text?.trim()) throw new Error(`${where}: line ${l.id} has no text`)
      if (!(l.in >= last && l.out > l.in && l.out <= shot.dur)) {
        throw new Error(`${where}: line ${l.id} [${l.in}, ${l.out}] must start after the previous line and end within the shot (${shot.dur}s)`)
      }
      last = l.out
    }
  }
  return board
}

// One key frame per subtitle line, taken 60% into the line, so each line is
// reviewed over the picture it plays on; a shot without lines shows its hero.
export function stillsOf(shot) {
  if (shot.stills?.length) return shot.stills.map((t, i) => ({ tag: `${shot.id}-${i + 1}`, t }))
  if (shot.lines?.length) {
    return shot.lines.map((l) => ({ tag: l.id, t: l.still ?? Number((l.in + (l.out - l.in) * 0.6).toFixed(2)) }))
  }
  return [{ tag: shot.id, t: shot.hero ?? Number((shot.dur * 0.6).toFixed(2)) }]
}

export function timeline(board) {
  let at = 0
  return board.shots.map((s) => {
    const row = { shot: s, start: at, end: at + s.dur }
    at += s.dur
    return row
  })
}

export const tc = (s) => {
  const m = Math.floor(s / 60)
  return `${m}:${(s - m * 60).toFixed(1).padStart(4, '0')}`
}
