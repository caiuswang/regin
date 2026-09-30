#!/usr/bin/env node
// Checks the storyboard and its rendered key frames. No args = every check;
// prints PASS/FAIL per check and exits 1 on any FAIL.
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

import { loadStoryboard, stillsOf, timeline } from './lib/storyboard.mjs'
import { ASPECT, subtitleAt } from './stage/lib/stage.js'

const FILM_DIR = dirname(fileURLToPath(import.meta.url))
const RUNTIME = [120, 180]
const MAX_SUBTITLE = 16
const MIN_CJK_SHARE = 0.8
const FRAME = { width: 3840, height: 2160 }
const PROBE = { w: 384, h: 216 }

const cjk = (s) => [...s].filter((c) => /[㐀-鿿]/u.test(c)).length
const latin = (s) => [...s].filter((c) => /[A-Za-z]/.test(c)).length
const cardTexts = (shot) => [...(shot.params?.lines ?? []), ...(shot.params?.card?.lines ?? [])].map((l) => l.text)

function size(png) {
  return execFileSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', png], { encoding: 'utf8' }).trim().split(',').map(Number)
}

// Grey pixels of a region: the bars at native resolution, so a thin stroke
// of subtitle ink is not averaged away; the picture downscaled.
function gray(png, vf) {
  return execFileSync('ffmpeg', ['-v', 'error', '-i', png, '-vf', `${vf},format=gray`, '-f', 'rawvideo', '-'], { maxBuffer: 1 << 26 })
}

function stats(buf, width, y0 = 0, y1 = buf.length / width) {
  let max = 0, sum = 0, clipped = 0, n = 0
  for (let y = y0; y < y1; y++) {
    for (let x = 0; x < width; x++) {
      const v = buf[y * width + x]
      max = Math.max(max, v)
      sum += v
      if (v >= 250) clipped++
      n++
    }
  }
  return { max, mean: sum / n, clipped: clipped / n }
}

function loadAll(outDir, sbPath) {
  const board = loadStoryboard(sbPath)
  const manifestPath = join(outDir, 'stills.json')
  const manifest = existsSync(manifestPath) ? JSON.parse(readFileSync(manifestPath, 'utf8')) : null
  return { board, manifest, outDir }
}

const CHECKS = {
  check_runtime_in_range({ board }) {
    const end = timeline(board).at(-1).end
    if (end < RUNTIME[0] || end > RUNTIME[1]) return [`runtime ${end.toFixed(1)}s is outside ${RUNTIME[0]}–${RUNTIME[1]}s`]
    return []
  },

  check_copy_is_short_chinese({ board }) {
    const errs = []
    let c = 0, l = 0
    for (const shot of board.shots) {
      for (const line of shot.lines ?? []) {
        const shown = [...line.text.replace(/[，。、；：？！\s]/gu, '')]
        if (shown.length > MAX_SUBTITLE) errs.push(`${line.id}: ${shown.length} characters on screen (max ${MAX_SUBTITLE}): ${line.text}`)
        if (!cjk(line.text)) errs.push(`${line.id}: no Chinese character: ${line.text}`)
        c += cjk(line.text)
        l += latin(line.text)
      }
      for (const text of cardTexts(shot)) { c += cjk(text); l += latin(text) }
      if ([...shot.picture].length < 30) errs.push(`${shot.id}: 画面 description is under 30 characters`)
    }
    if (c / (c + l) < MIN_CJK_SHARE) errs.push(`CJK share ${(c / (c + l)).toFixed(2)} < ${MIN_CJK_SHARE}`)
    return errs
  },

  check_stills_cover_every_line({ board, manifest, outDir }) {
    if (!manifest) return ['no stills.json — run node film/render.mjs']
    const errs = []
    for (const shot of board.shots) {
      const want = stillsOf(shot)
      const got = manifest.shots[shot.id] ?? []
      const tags = got.map((s) => s.tag).join(',')
      if (want.map((s) => s.tag).join(',') !== tags) { errs.push(`${shot.id}: stills [${tags}] do not match the storyboard's [${want.map((s) => s.tag)}]`); continue }
      for (const s of got) {
        const expected = subtitleAt(shot.lines, s.t)?.text ?? null
        if (s.subtitle !== expected) errs.push(`${s.tag}: rendered subtitle ${JSON.stringify(s.subtitle)} ≠ storyboard ${JSON.stringify(expected)} at t=${s.t}`)
        if (!existsSync(join(outDir, s.png)) || !existsSync(join(outDir, s.jpg))) errs.push(`${s.tag}: missing ${s.png} or ${s.jpg}`)
      }
      for (const line of shot.lines ?? []) {
        if (!got.some((s) => s.t >= line.in && s.t < line.out)) errs.push(`${line.id}: no key frame falls inside the line`)
      }
    }
    return errs
  },

  check_frames_letterboxed_and_exposed({ manifest, outDir }) {
    if (!manifest) return ['no stills.json — run node film/render.mjs']
    const errs = []
    const bar = Math.round((FRAME.height - FRAME.width / ASPECT) / 2)
    const barRows = Math.floor((bar / FRAME.height) * PROBE.h)
    for (const rowsOf of Object.values(manifest.shots)) {
      for (const s of rowsOf) {
        const png = join(outDir, s.png)
        const [w, h] = size(png)
        if (w !== FRAME.width || h !== FRAME.height) { errs.push(`${s.tag}: ${w}x${h}, expected ${FRAME.width}x${FRAME.height}`); continue }
        const top = stats(gray(png, `crop=${w}:${bar - 2}:0:0`), w)
        const bottom = stats(gray(png, `crop=${w}:${bar - 2}:0:${h - bar + 2}`), w)
        const active = stats(gray(png, `scale=${PROBE.w}:${PROBE.h}:flags=area`), PROBE.w, barRows + 1, PROBE.h - barRows - 1)
        if (top.max > 3) errs.push(`${s.tag}: top bar is not black (max ${top.max})`)
        if (s.subtitle && bottom.max < 200) errs.push(`${s.tag}: subtitle ${JSON.stringify(s.subtitle)} is not at full strength in the bottom bar (max ${bottom.max})`)
        if (!s.subtitle && bottom.max > 3) errs.push(`${s.tag}: bottom bar has ink with no subtitle (max ${bottom.max})`)
        if (active.mean < 2 || active.mean > 170) errs.push(`${s.tag}: active-area mean luma ${active.mean.toFixed(1)} outside 2–170`)
        if (active.clipped > 0.03) errs.push(`${s.tag}: ${(active.clipped * 100).toFixed(1)}% of the picture is clipped white`)
      }
    }
    return errs
  },

  check_review_sheet({ board, manifest, outDir }) {
    const path = join(outDir, 'storyboard.html')
    if (!existsSync(path)) return ['no storyboard.html — run node film/render.mjs']
    const html = readFileSync(path, 'utf8')
    const errs = []
    for (const shot of board.shots) {
      if (!html.includes(`id="${shot.id}"`)) errs.push(`${shot.id}: missing from the sheet`)
      for (const line of shot.lines ?? []) if (!html.includes(line.text)) errs.push(`${line.id}: line text missing from the sheet`)
      for (const s of manifest?.shots[shot.id] ?? []) if (!html.includes(s.jpg)) errs.push(`${s.tag}: frame missing from the sheet`)
    }
    return errs
  },
}

function main() {
  const { values, positionals } = parseArgs({ options: { out: { type: 'string' }, storyboard: { type: 'string' } }, allowPositionals: true })
  const ctx = loadAll(resolve(values.out ?? join(FILM_DIR, 'out')), resolve(values.storyboard ?? join(FILM_DIR, 'storyboard.zh.json')))
  const names = positionals.length ? positionals : Object.keys(CHECKS)
  let failed = 0
  for (const name of names) {
    if (!CHECKS[name]) { console.log(`FAIL ${name}: no such check`); failed++; continue }
    const errs = CHECKS[name](ctx)
    console.log(`${errs.length ? 'FAIL' : 'PASS'} ${name}`)
    for (const e of errs) console.log(`     ${e}`)
    if (errs.length) failed++
  }
  process.exit(failed ? 1 : 0)
}

main()
