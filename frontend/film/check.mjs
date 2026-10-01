#!/usr/bin/env node
// Checks the storyboard, its rendered key frames and the rendered film. No
// args = every check; prints PASS/FAIL per check and exits 1 on any FAIL.
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

import { loadStoryboard, slugOf, sourceDigest, stillsOf, timeline } from './lib/storyboard.mjs'
import { cutOf } from './stage/lib/cut.js'
import { ASPECT, subtitleAt } from './stage/lib/stage.js'

const FILM_DIR = dirname(fileURLToPath(import.meta.url))
const RUNTIME = [120, 180]
const MAX_SUBTITLE = 16
const MIN_CJK_SHARE = 0.8
const FRAME = { width: 3840, height: 2160 }
const PROBE = { w: 384, h: 216 }
const FILM_OUTPUTS = { '2160p': [3840, 2160], '1080p': [1920, 1080] }
// The film against its key frames, compared at 960×540 in grey. Key frames
// sit on the frame grid, so the two differ only by encoding: ~40 dB and up.
// A key frame one frame off its film frame in a moving shot drops below 35.
const MATCH = { w: 960, h: 540, picturePsnr: 35, barPsnr: 40, ink: 120 }
// A flash: one frame off both neighbours by this much (0–255 luma), while
// the neighbours agree.
const FLASH = { jump: 12, agree: 4 }
const FADE_BLACK = 3

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
  const read = (p) => (existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : null)
  const manifest = read(join(outDir, 'stills.json'))
  const film = read(join(outDir, 'film', `${slugOf(board)}.json`))
  return { board, manifest, film, outDir, sbPath }
}

function probe(path) {
  const out = execFileSync('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries',
    'stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_frames,color_primaries,color_transfer,color_space', '-of', 'json', path], { encoding: 'utf8' })
  return JSON.parse(out).streams[0]
}

const filmPath = (ctx, tag) => join(ctx.outDir, 'film', ctx.film.outputs.find((o) => o.tag === tag).path)

// Full-range grey frames of a video, scaled, after an optional select.
function videoGray(path, { w, h, select }) {
  const vf = `${select ? `select='${select}',` : ''}scale=${w}:${h}:flags=area:out_range=pc,format=gray`
  return execFileSync('ffmpeg', ['-v', 'error', '-i', path, '-vf', vf, '-fps_mode', 'passthrough', '-f', 'rawvideo', '-'], { maxBuffer: 1 << 30 })
}

function psnr(a, b, w, y0, y1) {
  let se = 0
  for (let i = y0 * w; i < y1 * w; i++) se += (a[i] - b[i]) ** 2
  const mse = se / ((y1 - y0) * w)
  return mse === 0 ? Infinity : 10 * Math.log10((255 * 255) / mse)
}

function rowsMax(buf, w, y0, y1) {
  let m = 0
  for (let i = y0 * w; i < y1 * w; i++) m = Math.max(m, buf[i])
  return m
}

function rowsMean(buf, w, y0, y1) {
  let sum = 0
  for (let i = y0 * w; i < y1 * w; i++) sum += buf[i]
  return sum / ((y1 - y0) * w)
}

function filmReady(ctx) {
  if (!ctx.film) return 'no film — run node film/film.mjs'
  if (ctx.film.partial) return 'the film on disk is a partial render — run node film/film.mjs without --shots/--frames'
  return null
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
      const want = stillsOf(shot, board.fps)
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

  check_film_encoded(ctx) {
    const blocked = filmReady(ctx)
    if (blocked) return [blocked]
    const { board, film, sbPath } = ctx
    const cut = cutOf(board)
    const errs = []
    if (film.digest !== sourceDigest(sbPath)) errs.push('stale: the storyboard or the stage changed after the film was rendered — re-run node film/film.mjs')
    for (const [tag, [w, h]] of Object.entries(FILM_OUTPUTS)) {
      const o = film.outputs.find((x) => x.tag === tag)
      if (!o) { errs.push(`${tag}: not rendered`); continue }
      const path = join(ctx.outDir, 'film', o.path)
      if (!existsSync(path)) { errs.push(`${tag}: ${o.path} is missing`); continue }
      const s = probe(path)
      const want = {
        codec_name: 'h264', pix_fmt: 'yuv420p', width: w, height: h, r_frame_rate: `${cut.fps}/1`, nb_frames: String(cut.frames),
        color_primaries: 'bt709', color_transfer: 'bt709', color_space: 'bt709',
      }
      for (const [k, v] of Object.entries(want)) if (s[k] !== v) errs.push(`${tag}: ${k} is ${s[k]}, expected ${v}`)
    }
    return errs
  },

  check_film_matches_key_frames(ctx) {
    const blocked = filmReady(ctx)
    if (blocked) return [blocked]
    if (!ctx.manifest) return ['no stills.json — run node film/render.mjs']
    const { board, manifest, outDir } = ctx
    const cut = cutOf(board)
    const { w, h } = MATCH
    const bar = Math.round((h - w / ASPECT) / 2)
    const wanted = []
    for (const r of cut.shots) {
      for (const s of manifest.shots[r.shot.id] ?? []) wanted.push({ ...s, n: Math.round((r.start + s.t) * cut.fps) })
    }
    wanted.sort((a, b) => a.n - b.n)
    const frames = videoGray(filmPath(ctx, '1080p'), { w, h, select: wanted.map((s) => `eq(n\\,${s.n})`).join('+') })
    const size = w * h
    if (frames.length !== size * wanted.length) return [`extracted ${frames.length / size} of ${wanted.length} frames`]
    const errs = []
    wanted.forEach((s, i) => {
      const film = frames.subarray(i * size, (i + 1) * size)
      const still = gray(join(outDir, s.png), `scale=${w}:${h}:flags=area:out_range=pc`)
      const pic = psnr(film, still, w, bar, h - bar)
      if (pic < MATCH.picturePsnr) errs.push(`${s.tag}: film frame ${s.n} differs from the key frame (picture PSNR ${pic.toFixed(1)} dB < ${MATCH.picturePsnr})`)
      const inkFilm = rowsMax(film, w, h - bar, h) >= MATCH.ink, inkStill = rowsMax(still, w, h - bar, h) >= MATCH.ink
      if (inkFilm !== inkStill) errs.push(`${s.tag}: subtitle ${inkStill ? 'missing from' : 'extra in'} film frame ${s.n}`)
      const sub = psnr(film, still, w, h - bar, h)
      if (inkFilm && inkStill && sub < MATCH.barPsnr) errs.push(`${s.tag}: film frame ${s.n} shows a different subtitle (bar PSNR ${sub.toFixed(1)} dB < ${MATCH.barPsnr})`)
    })
    return errs
  },

  check_film_transitions(ctx) {
    const blocked = filmReady(ctx)
    if (blocked) return [blocked]
    const cut = cutOf(ctx.board)
    const w = 192, h = 108
    const bar = Math.ceil((h - w / ASPECT) / 2) + 1
    const frames = videoGray(filmPath(ctx, '1080p'), { w, h })
    const n = frames.length / (w * h)
    if (n !== cut.frames) return [`decoded ${n} frames, the cut has ${cut.frames}`]
    const luma = Array.from({ length: n }, (_, i) => rowsMean(frames.subarray(i * w * h, (i + 1) * w * h), w, bar, h - bar))
    const errs = []
    for (const r of cut.shots) {
      if (r.shot.enter?.type === 'fade' && luma[r.f0] > FADE_BLACK) errs.push(`${r.shot.id}: fades in, but its first frame ${r.f0} has luma ${luma[r.f0].toFixed(1)}`)
      if (r.shot.exit?.type === 'fade' && luma[r.f1 - 1] > FADE_BLACK) errs.push(`${r.shot.id}: fades out, but its last frame ${r.f1 - 1} has luma ${luma[r.f1 - 1].toFixed(1)}`)
    }
    for (let i = 1; i < n - 1; i++) {
      const a = luma[i] - luma[i - 1], b = luma[i] - luma[i + 1]
      if (Math.abs(a) > FLASH.jump && Math.abs(b) > FLASH.jump && Math.sign(a) === Math.sign(b) && Math.abs(luma[i - 1] - luma[i + 1]) < FLASH.agree) {
        errs.push(`frame ${i}: a one-frame ${a > 0 ? 'flash' : 'drop'} (luma ${luma[i - 1].toFixed(1)} → ${luma[i].toFixed(1)} → ${luma[i + 1].toFixed(1)})`)
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
