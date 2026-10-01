#!/usr/bin/env node
// Renders the storyboard's key frames (one per subtitle line, or the shot's
// hero moment) at 4K, plus the review sheet that pairs each frame with its
// 画面 and 字幕. The film itself is rendered from the same stage, frame by
// frame, by film.mjs.
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

import { openStage, SIZES } from './lib/host.mjs'
import { writeReview } from './lib/review.mjs'
import { loadStoryboard, stillsOf } from './lib/storyboard.mjs'

const FILM_DIR = dirname(fileURLToPath(import.meta.url))

async function main() {
  const { values } = parseArgs({
    options: {
      storyboard: { type: 'string' }, out: { type: 'string' }, shots: { type: 'string' },
      size: { type: 'string', default: '4k' }, at: { type: 'string' },
    },
  })
  if (!SIZES[values.size]) throw new Error(`--size must be one of ${Object.keys(SIZES).join(', ')}`)
  const sbPath = resolve(values.storyboard ?? join(FILM_DIR, 'storyboard.zh.json'))
  const outDir = resolve(values.out ?? join(FILM_DIR, 'out'))
  const board = loadStoryboard(sbPath)
  const only = values.shots ? new Set(values.shots.split(',')) : null
  if (only) for (const id of only) if (!board.shots.some((s) => s.id === id)) throw new Error(`--shots: no shot "${id}"`)

  const stillsDir = join(outDir, 'stills'), reviewDir = join(outDir, 'review')
  if (!only && values.at == null) for (const d of [stillsDir, reviewDir]) rmSync(d, { recursive: true, force: true })
  mkdirSync(stillsDir, { recursive: true })
  mkdirSync(reviewDir, { recursive: true })

  const stage = await openStage(values.size)
  try {
    const info = await stage.page.evaluate((b) => window.film.load(b), board)
    const manifestPath = join(outDir, 'stills.json')
    let manifest
    try { manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) } catch { manifest = null }
    if (!manifest || manifest.storyboard !== sbPath || !only) manifest = { storyboard: sbPath, shots: {} }
    manifest.runtime = info.runtime
    manifest.frame = { width: SIZES[values.size][0], height: SIZES[values.size][1], bar: info.bar }
    manifest.renderer = stage.renderer
    for (const shot of board.shots) {
      if (only && !only.has(shot.id)) continue
      const stills = values.at != null ? [{ tag: `${shot.id}-at`, t: Number(values.at) }] : stillsOf(shot, board.fps)
      const rows = []
      for (const s of stills) {
        const t0 = Date.now()
        const r = await stage.page.evaluate(([id, t]) => window.film.render(id, t), [shot.id, s.t])
        if (stage.errors.length) throw new Error(`shot ${shot.id} @${s.t}s:\n${stage.errors.join('\n')}`)
        const png = join(stillsDir, `${s.tag}.png`), jpg = join(reviewDir, `${s.tag}.jpg`)
        writeFileSync(png, Buffer.from(await stage.page.evaluate(() => window.film.capture()), 'base64'))
        writeFileSync(jpg, Buffer.from(await stage.page.evaluate(() => window.film.preview(1920)), 'base64'))
        rows.push({ tag: s.tag, t: s.t, subtitle: r.subtitle, png: `stills/${s.tag}.png`, jpg: `review/${s.tag}.jpg` })
        console.log(`${s.tag.padEnd(16)} t=${String(s.t).padEnd(6)} ${String(Date.now() - t0).padStart(5)}ms  ${r.subtitle ?? ''}`)
      }
      if (values.at == null) manifest.shots[shot.id] = rows
    }
    writeFileSync(manifestPath, JSON.stringify(manifest, null, 2))
    writeReview(board, manifest, outDir)
    console.log(`\n${Object.keys(manifest.shots).length} shots · runtime ${info.runtime.toFixed(1)}s · ${stage.renderer}`)
    console.log(`review: ${join(outDir, 'storyboard.html')}`)
  } finally {
    await stage.close()
  }
}

main().catch((e) => { console.error(e.message ?? e); process.exit(1) })
