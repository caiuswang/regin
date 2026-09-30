#!/usr/bin/env node
// node promo/check.mjs [--out <dir>] [<check-name> …] — no names runs every
// check. Prints PASS/FAIL per check; exits 1 on any FAIL.
import { execFileSync, spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { parseArgs } from 'node:util'

import { DEFAULT_OUT, FRAME, PROMO_DIR, PROMO_USER, PY, ROOT, VIEWPORT, liveStores, py } from './lib/env.mjs'
import { CAPTIONS, placedLines } from './lib/mux.mjs'

const NAME = 'regin-promo-zh'
const { values, positionals } = parseArgs({ options: { out: { type: 'string' } }, allowPositionals: true })
const OUT = values.out ? resolve(values.out) : DEFAULT_OUT
const MP4 = join(OUT, `${NAME}.mp4`)

let manifestCache
const manifest = () => (manifestCache ??= JSON.parse(readFileSync(join(OUT, 'manifest.json'), 'utf8')))
const storyboard = () => JSON.parse(readFileSync(manifest().storyboard, 'utf8'))
const runtime = () => manifest().scenes.at(-1).end_s

class Fail extends Error {}
const must = (cond, msg) => { if (!cond) throw new Fail(msg) }

// Past these, two consecutive frames of the film differ by a cut or a flash
// rather than by a dissolve, a glide or a scroll. A white frame between two
// dark pages scores ~90 and moves the mean luma by ~200.
const MAX_SCENE_SCORE = 10
const MAX_LUMA_STEP = 8
// A line's subtitle band, read through the line, may change by at most this
// mean per-pixel difference (0–255). The band is opaque, so a steady one
// scores ~0 whatever scrolls or zooms behind it; a band that vanished or
// changed text scores 20+.
const MAX_BAND_DRIFT = 8

function ffmpegStderr(args) {
  const r = spawnSync('ffmpeg', ['-hide_banner', '-nostats', ...args], { encoding: 'utf8', maxBuffer: 64 << 20 })
  if (r.status !== 0) throw new Error(`ffmpeg ${args.join(' ')} failed:\n${r.stderr.slice(-2000)}`)
  return r.stderr
}

const ROUTES = [
  ['/overview', /^\/overview$/], ['/features', /^\/features$/], ['/features/:id', /^\/features\/\d+$/],
  ['/tasks', /^\/tasks$/], ['/tasks/:id', /^\/tasks\/\d+$/], ['/defects', /^\/defects$/],
  ['/defects/:id', /^\/defects\/\d+$/], ['/trace/sessions', /^\/trace\/sessions$/],
  ['/trace/sessions/:id', /^\/trace\/sessions\/[^/]+$/],
]

const parseSrt = (text) => text.trim().split(/\r?\n\r?\n/).map((block) => {
  const [, times, ...body] = block.split(/\r?\n/)
  const [a, b] = times.split(' --> ').map((t) => {
    const [h, m, s] = t.replace(',', '.').split(':')
    return Number(h) * 3600 + Number(m) * 60 + Number(s)
  })
  return { start: a, end: b, text: body.join('\n') }
})

// Reads every frame of the film, grey at half the layout's CSS size, cropped
// to the rows the subtitle band occupies, and for each line compares its
// band's box in every frame the line holds the band — through any dissolve
// that outran its audio — with the first one. Returns each
// line's worst mean per-pixel difference. Every frame, not a sample: a band
// that blinks out does so for a frame or two.
function bandDrift(lines) {
  const FPS = 30, W = VIEWPORT.width / 2
  const top = Math.floor(Math.min(...lines.map((l) => l.band.y)) / 2) - 2
  const bottom = Math.ceil(Math.max(...lines.map((l) => l.band.y + l.band.h)) / 2) + 2
  const rows = bottom - top, size = W * rows
  const r = spawnSync('ffmpeg', ['-v', 'error', '-i', MP4, '-an', '-vf',
    `scale=${W}:${VIEWPORT.height / 2},crop=${W}:${rows}:0:${top},format=gray`, '-f', 'rawvideo', '-'],
  { maxBuffer: 1024 ** 3 })
  if (r.status !== 0) throw new Error(`ffmpeg sampling failed: ${r.stderr}`)
  const frames = Math.floor(r.stdout.length / size)
  return lines.map((l) => {
    // Inset from the band's edges: the pointer may graze its top, and the
    // corners are anti-aliased against whatever the page shows behind them.
    const b = l.band, x0 = Math.round((b.x + 12) / 2), x1 = Math.round((b.x + b.w - 12) / 2)
    const y0 = Math.round((b.y + 10) / 2) - top, y1 = Math.round((b.y + b.h - 4) / 2) - top
    const k0 = Math.ceil((l.start_s + 0.25) * FPS), k1 = Math.min(frames - 1, Math.floor((l.shown_until - 0.05) * FPS))
    let worst = 0, at = l.start_s
    for (let k = k0 + 1; k <= k1; k += 1) {
      let sum = 0
      for (let y = y0; y < y1; y += 1) {
        const ra = k0 * size + y * W, rb = k * size + y * W
        for (let x = x0; x < x1; x += 1) sum += Math.abs(r.stdout[ra + x] - r.stdout[rb + x])
      }
      const d = sum / Math.max(1, (x1 - x0) * (y1 - y0))
      if (d > worst) { worst = d; at = k / FPS }
    }
    return { id: l.id, worst, at }
  })
}

const checks = {
  check_runtime_and_streams() {
    const probe = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', MP4],
      { encoding: 'utf8' }))
    const by = (t) => probe.streams.filter((s) => s.codec_type === t)
    const [v] = by('video'), [a] = by('audio')
    must(by('video').length === 1 && v.codec_name === 'h264', `video streams: ${by('video').map((x) => x.codec_name)}`)
    must(v.width === FRAME.width && v.height === FRAME.height, `video is ${v.width}x${v.height}`)
    must(v.r_frame_rate === '30/1' && v.avg_frame_rate === '30/1', `frame rate ${v.r_frame_rate} avg ${v.avg_frame_rate}`)
    must(by('audio').length === 1 && a.codec_name === 'aac', `audio streams: ${by('audio').map((x) => x.codec_name)}`)
    must(by('subtitle').length === 0, `subtitle streams: ${by('subtitle').map((x) => x.codec_name)} — the subtitles are burned in`)
    const d = Number(probe.format.duration)
    must(d >= 180 && d <= 300, `duration ${d.toFixed(1)}s is outside 180–300s`)
    return `h264 ${v.width}x${v.height}@30 + aac, no subtitle track, ${d.toFixed(1)}s`
  },

  // Every line is on screen exactly once: drawn into the frames and nowhere
  // else — no soft track for a player to lay over it, no sidecar a player
  // auto-loads beside the mp4 — one band in the page, and that band held
  // steady for the whole line.
  check_subtitles_burned_once() {
    const probe = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-show_streams', '-of', 'json', MP4], { encoding: 'utf8' }))
    const soft = probe.streams.filter((x) => x.codec_type === 'subtitle')
    must(soft.length === 0, `the mp4 carries ${soft.length} subtitle track(s) (${soft.map((x) => x.codec_name)}) over the burned-in lines`)
    const sidecars = readdirSync(OUT).filter((f) => new RegExp(`^${NAME}(\\..+)?\\.(srt|vtt|ass|ssa|sub)$`, 'i').test(f))
    must(sidecars.length === 0, `sidecar(s) a player auto-loads beside ${NAME}.mp4: ${sidecars.join(', ')}`)
    const lines = manifest().scenes.flatMap((sc) => sc.lines)
    const doubled = lines.filter((l) => l.subtitle_layers !== 1)
    must(doubled.length === 0, `lines drawn with ≠1 band: ${doubled.map((l) => `${l.id}×${l.subtitle_layers}`).join(', ')}`)
    const drift = bandDrift(lines)
    const bad = drift.filter((d) => d.worst > MAX_BAND_DRIFT)
    must(bad.length === 0, `band left or changed mid-line: ${bad.map((d) => `${d.id} Δ${d.worst.toFixed(1)} at ${d.at.toFixed(2)}s`).join(', ')}`)
    const worst = drift.reduce((m, d) => (d.worst > m.worst ? d : m), { worst: 0, id: '-' })
    return `no soft track, no sidecar, ${lines.length} lines × 1 band, worst band drift ${worst.worst.toFixed(1)} (${worst.id})`
  },

  check_rail_collapsed() {
    const scenes = manifest().scenes.filter((sc) => sc.surface !== 'card')
    const open = scenes.flatMap((sc) => [
      ...sc.lines.filter((l) => l.rail !== 'collapsed').map((l) => `${l.id}: ${l.rail}`),
      ...(sc.rail_at_hero === 'collapsed' ? [] : [`${sc.id} hero: ${sc.rail_at_hero}`]),
    ])
    must(open.length === 0, `the rail was not collapsed at: ${open.join(', ')}`)
    const n = scenes.reduce((k, sc) => k + sc.lines.length + 1, 0)
    return `collapsed at all ${n} line starts and hero moments of ${scenes.length} scenes`
  },

  check_no_flash_or_hard_cut() {
    const log = spawnSync('ffmpeg', ['-hide_banner', '-nostats', '-i', MP4, '-an', '-vf',
      'scale=640:360,scdet=threshold=0,signalstats,metadata=print:file=-', '-f', 'null', '-'],
    { encoding: 'utf8', maxBuffer: 256 << 20 })
    if (log.status !== 0) throw new Error(`ffmpeg scdet failed:\n${log.stderr.slice(-1500)}`)
    const rows = []
    for (const line of log.stdout.split('\n')) {
      const f = line.match(/pts_time:([\d.]+)/)
      if (f) { rows.push({ t: Number(f[1]), score: 0, y: 0 }); continue }
      const m = line.match(/lavfi\.(scd\.score|signalstats\.YAVG)=([\d.]+)/)
      if (m && rows.length) rows.at(-1)[m[1] === 'scd.score' ? 'score' : 'y'] = Number(m[2])
    }
    must(rows.length > 0, 'no frames measured')
    const cuts = rows.filter((r) => r.score > MAX_SCENE_SCORE)
    const flashes = rows.slice(1).map((r, i) => ({ t: r.t, dy: Math.abs(r.y - rows[i].y) })).filter((r) => r.dy > MAX_LUMA_STEP)
    must(cuts.length === 0, `hard cuts (scene score > ${MAX_SCENE_SCORE}) at ${cuts.slice(0, 10).map((r) => `${r.t.toFixed(2)}s (${r.score.toFixed(1)})`).join(', ')}`)
    must(flashes.length === 0, `luma jumps > ${MAX_LUMA_STEP} at ${flashes.slice(0, 10).map((r) => `${r.t.toFixed(2)}s (Δ${r.dy.toFixed(0)})`).join(', ')}`)
    const top = rows.reduce((m, r) => (r.score > m.score ? r : m))
    return `${rows.length} frames; max scene score ${top.score.toFixed(2)} at ${top.t.toFixed(2)}s; no luma step > ${MAX_LUMA_STEP}`
  },

  check_route_coverage_and_share() {
    const scenes = manifest().scenes
    const visited = scenes.flatMap((s) => s.visited)
    const outside = visited.filter((p) => !ROUTES.some(([, re]) => re.test(p)))
    must(outside.length === 0, `routes outside Pipeline/Session: ${[...new Set(outside)].join(', ')}`)
    const missing = ROUTES.filter(([, re]) => !visited.some((p) => re.test(p))).map(([n]) => n)
    must(missing.length === 0, `routes never visited: ${missing.join(', ')}`)
    const total = runtime()
    const share = { card: 0, pipeline: 0, session: 0 }
    for (const s of scenes) share[s.surface] += s.end_s - s.start_s
    must(share.card <= 30, `cards run ${share.card.toFixed(1)}s (> 30s)`)
    must(share.pipeline / total >= 0.45, `pipeline share ${(100 * share.pipeline / total).toFixed(1)}% (< 45%)`)
    must(share.session / total >= 0.25, `session share ${(100 * share.session / total).toFixed(1)}% (< 25%)`)
    return `9/9 routes; cards ${share.card.toFixed(1)}s, pipeline ${(100 * share.pipeline / total).toFixed(0)}%, session ${(100 * share.session / total).toFixed(0)}%`
  },

  check_narration_is_chinese() {
    const lines = storyboard().scenes.flatMap((s) => s.lines)
    let han = 0, latin = 0
    for (const l of lines) {
      must(/\p{Script=Han}/u.test(l.text), `line ${l.id} has no CJK: ${l.text}`)
      must([...l.text].length <= 60, `line ${l.id} is ${[...l.text].length} chars (> 60)`)
      han += (l.text.match(/\p{Script=Han}/gu) ?? []).length
      latin += (l.text.match(/[A-Za-z]/g) ?? []).length
    }
    const ratio = han / (han + latin)
    must(ratio >= 0.75, `CJK share ${(100 * ratio).toFixed(1)}% (< 75%)`)
    return `${lines.length} lines, CJK ${(100 * ratio).toFixed(1)}% (D10 is the verifier's)`
  },

  check_subtitles_match_audio() {
    const placed = placedLines(manifest(), storyboard())
    const cues = parseSrt(readFileSync(join(OUT, CAPTIONS), 'utf8'))
    must(cues.length === placed.length, `${cues.length} cues for ${placed.length} lines`)
    const log = ffmpegStderr(['-i', MP4, '-map', '0:a', '-af', 'silencedetect=noise=-45dB:d=0.2', '-f', 'null', '-'])
    const starts = [...log.matchAll(/silence_start: ([\d.]+)/g)].map((m) => Number(m[1]))
    const onsets = [...log.matchAll(/silence_end: ([\d.]+)/g)].map((m) => Number(m[1]))
    if (!starts.length || starts[0] > 0.01) onsets.unshift(0)
    let worst = 0
    placed.forEach((l, i) => {
      const c = cues[i]
      must(Math.abs(c.start - l.start_s) < 0.0015 && Math.abs(c.end - (l.start_s + l.dur_s)) < 0.0015,
        `cue ${i + 1} ${c.start}–${c.end} ≠ manifest ${l.start_s.toFixed(3)}+${l.dur_s.toFixed(3)} (${l.id})`)
      must(c.text === l.text, `cue ${i + 1} text differs from line ${l.id}`)
      if (i > 0) must(cues[i - 1].end <= c.start, `cues ${i} and ${i + 1} overlap`)
      const off = Math.min(...onsets.map((o) => Math.abs(o - c.start)))
      must(off <= 0.3, `cue ${i + 1} (${l.id}) starts ${off.toFixed(2)}s from the nearest speech onset`)
      worst = Math.max(worst, off)
    })
    return `${cues.length} cues, worst onset offset ${worst.toFixed(3)}s`
  },

  check_audio_loudness() {
    const log = ffmpegStderr(['-i', MP4, '-map', '0:a', '-af', 'ebur128', '-f', 'null', '-'])
    const m = log.slice(log.lastIndexOf('Summary:')).match(/I:\s+(-?[\d.]+) LUFS/)
    must(m, 'no ebur128 summary')
    const lufs = Number(m[1])
    must(lufs >= -18 && lufs <= -14, `integrated loudness ${lufs} LUFS is outside −18…−14`)
    return `${lufs} LUFS`
  },

  check_pins_refuse_missing_id() {
    const { pins, stores } = manifest()
    const status = JSON.parse(py(`
import json, sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(json.dumps([(c.execute("select status from sessions where trace_id=?", (t,)).fetchone() or [None])[0] for t in sys.argv[2:]]))
`, [stores.snapshot, ...pins.sessions]))
    pins.sessions.forEach((id, i) => must(status[i] === 'ended', `pinned session ${id} is ${status[i] ?? 'missing'}, not ended`))

    const dir = mkdtempSync(join(tmpdir(), 'regin-promo-refuse-'))
    try {
      const sb = storyboard()
      sb.pins.feature = 999999
      const sbPath = join(dir, 'storyboard.json'), outDir = join(dir, 'out')
      writeFileSync(sbPath, JSON.stringify(sb))
      const r = spawnSync(process.execPath, [join(PROMO_DIR, 'build.mjs'), '--storyboard', sbPath, '--out', outDir],
        { encoding: 'utf8', cwd: resolve(PROMO_DIR, '..') })
      must(r.status !== 0, 'build with feature=999999 exited 0')
      must(/feature/.test(r.stderr) && /999999/.test(r.stderr), `stderr does not name the pin:\n${r.stderr.slice(-600)}`)
      const written = existsSync(outDir) ? readdirSync(outDir) : []
      must(!written.some((f) => f.endsWith('.mp4')), 'an mp4 was written despite the refusal')
      must(!written.includes('frames'), 'recording started before the pin was refused')
      return `sessions ended; refusal: ${r.stderr.trim().split('\n').at(-1)}`
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  },

  check_hero_frames_legible() {
    const dir = mkdtempSync(join(tmpdir(), 'regin-promo-ssim-'))
    const scores = []
    try {
      for (const s of manifest().scenes.filter((x) => x.surface !== 'card')) {
        must(s.hero_s !== null, `scene ${s.id} has no hero moment`)
        const frame = join(dir, `${s.id}.png`)
        execFileSync('ffmpeg', ['-v', 'error', '-y', '-ss', s.hero_s.toFixed(3), '-i', MP4, '-frames:v', '1', frame])
        const log = ffmpegStderr(['-i', frame, '-i', join(OUT, 'hero', `${s.id}.png`), '-lavfi',
          `[1:v]scale=${FRAME.width}:${FRAME.height}[r];[0:v][r]ssim`, '-f', 'null', '-'])
        const score = Number(log.match(/All:([\d.]+)/)[1])
        scores.push(`${s.id} ${score.toFixed(3)}`)
        must(score >= 0.9, `scene ${s.id}: SSIM ${score.toFixed(3)} < 0.90 at ${s.hero_s.toFixed(2)}s`)
      }
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
    return scores.join(', ')
  },

  check_clean_frames() {
    const m = manifest()
    must(m.console_errors.length === 0, `console errors:\n  ${m.console_errors.slice(0, 8).join('\n  ')}`)
    const dirty = m.scenes.filter((s) => s.hero_errors.length)
    must(dirty.length === 0, `error states at hero: ${dirty.map((s) => `${s.id}: ${s.hero_errors.join('; ')}`).join(' | ')}`)
    const texts = readdirSync(join(OUT, 'text')).filter((f) => f.endsWith('.txt')).map((f) => join(OUT, 'text', f))
    must(texts.length === m.scenes.filter((s) => s.surface !== 'card').length, `${texts.length} text dumps for the non-card scenes`)
    const env = { ...process.env, PYTHONPATH: ROOT }
    delete env.SKIP_SECRET_SCAN
    const r = spawnSync(PY, [join(ROOT, 'scripts/check_secrets.py'), ...texts], { cwd: ROOT, env, encoding: 'utf8' })
    must(r.status === 0, `check_secrets.py exit ${r.status}:\n${r.stdout}${r.stderr}`)
    return `0 console errors, ${m.scenes.length} scenes clean, ${texts.length} text dumps pass check_secrets`
  },

  check_snapshot_isolation() {
    const m = manifest()
    must(m.blocked_writes.length === 0, `blocked writes: ${[...new Set(m.blocked_writes)].join(', ')}`)
    const { before, after } = m.snapshot_counts
    must(JSON.stringify(before) === JSON.stringify(after),
      `snapshot counts changed: ${JSON.stringify(before)} → ${JSON.stringify(after)}`)
    const t = m.tmux_sessions
    must(JSON.stringify(t.before) === JSON.stringify(t.after),
      `tmux sessions changed: +[${t.after.filter((x) => !t.before.includes(x))}] -[${t.before.filter((x) => !t.after.includes(x))}]`)
    const live = liveStores().trace
    must(live !== m.stores.snapshot, 'the snapshot path is the live DB')
    const hasUser = (db) => py(`
import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(c.execute("select count(*) from users where username=?", (sys.argv[2],)).fetchone()[0])
`, [db, PROMO_USER]).trim() !== '0'
    must(hasUser(m.stores.snapshot), `${PROMO_USER} is missing from the snapshot`)
    must(!hasUser(live), `${PROMO_USER} exists in the live DB ${live}`)
    return `0 blocked writes; counts ${JSON.stringify(after)} unchanged; ${t.after.length} tmux sessions unchanged; ${PROMO_USER} snapshot-only`
  },
}

const names = positionals.length ? positionals : Object.keys(checks)
const unknown = names.filter((n) => !checks[n])
if (unknown.length) {
  console.error(`unknown check(s): ${unknown.join(', ')}\nknown: ${Object.keys(checks).join(', ')}`)
  process.exit(2)
}
let failed = 0
for (const name of names) {
  try {
    console.log(`PASS ${name} — ${checks[name]()}`)
  } catch (e) {
    failed += 1
    console.log(`FAIL ${name} — ${e instanceof Fail ? e.message : e.stack}`)
  }
}
process.exit(failed ? 1 : 0)
