#!/usr/bin/env node
// Renders the film: every frame of the cut, at the storyboard's fps, from the
// same stage as the key frames. Raw frames go straight into one ffmpeg that
// writes the 4K master and the 1080p cut side by side, so no frame touches
// the disk uncompressed.
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

import { openStage, SIZES } from './lib/host.mjs'
import { loadStoryboard, slugOf, sourceDigest } from './lib/storyboard.mjs'
import { cutOf } from './stage/lib/cut.js'

const FILM_DIR = dirname(fileURLToPath(import.meta.url))
// CRF per output: the master keeps the grain, the 1080p cut is the one shared.
const OUTPUTS = [
  { tag: '2160p', w: 3840, h: 2160, crf: 16, level: '5.1' },
  { tag: '1080p', w: 1920, h: 1080, crf: 18, level: '4.1' },
]

function ffmpegArgs(W, H, fps, outputs) {
  const branches = outputs.map((o, i) =>
    `[s${i}]scale=${o.w}:${o.h}:flags=lanczos+accurate_rnd+full_chroma_int:out_color_matrix=bt709:out_range=tv,format=yuv420p,` +
    `setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv[v${i}]`)
  // GL reads rows bottom first, hence the flip.
  const graph = `[0:v]vflip,split=${outputs.length}${outputs.map((_, i) => `[s${i}]`).join('')};${branches.join(';')}`
  const args = [
    '-hide_banner', '-loglevel', 'error', '-y',
    '-f', 'rawvideo', '-pix_fmt', 'rgba', '-video_size', `${W}x${H}`, '-framerate', String(fps), '-i', 'pipe:0',
    '-filter_complex', graph,
  ]
  for (const [i, o] of outputs.entries()) {
    args.push(
      '-map', `[v${i}]`, '-c:v', 'libx264', '-preset', 'slow', '-crf', String(o.crf), '-tune', 'film',
      '-profile:v', 'high', '-level:v', o.level, '-pix_fmt', 'yuv420p',
      '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-color_range', 'tv',
      '-movflags', '+faststart', '-f', 'mp4', o.part,
    )
  }
  return args
}

function selectFrames(cut, { shots, frames }) {
  if (frames) {
    const [a, b] = frames.split('-').map(Number)
    if (!(a >= 0 && b > a && b <= cut.frames)) throw new Error(`--frames must be a-b within 0-${cut.frames}`)
    return Array.from({ length: b - a }, (_, i) => a + i)
  }
  if (!shots) return Array.from({ length: cut.frames }, (_, i) => i)
  const ids = shots.split(',')
  const out = []
  for (const id of ids) {
    const r = cut.shots.find((s) => s.shot.id === id)
    if (!r) throw new Error(`--shots: no shot "${id}"`)
    for (let n = r.f0; n < r.f1; n++) out.push(n)
  }
  return out
}

const clock = (s) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`

async function main() {
  const { values } = parseArgs({
    options: {
      storyboard: { type: 'string' }, out: { type: 'string' }, size: { type: 'string', default: '4k' },
      shots: { type: 'string' }, frames: { type: 'string' },
    },
  })
  if (!SIZES[values.size]) throw new Error(`--size must be one of ${Object.keys(SIZES).join(', ')}`)
  const sbPath = resolve(values.storyboard ?? join(FILM_DIR, 'storyboard.zh.json'))
  const outDir = resolve(values.out ?? join(FILM_DIR, 'out'), 'film')
  const board = loadStoryboard(sbPath)
  const digest = sourceDigest(sbPath)
  const cut = cutOf(board)
  const frames = selectFrames(cut, values)
  const partial = frames.length !== cut.frames
  const name = partial ? 'preview' : slugOf(board)
  const [W, H] = SIZES[values.size]
  const outputs = OUTPUTS.filter((o) => o.w <= W).map((o) => {
    const path = join(outDir, `${name}.${o.tag}.mp4`)
    return { ...o, path, part: `${path}.part` }
  })
  mkdirSync(outDir, { recursive: true })

  const ff = spawn('ffmpeg', ffmpegArgs(W, H, cut.fps, outputs), { stdio: ['pipe', 'inherit', 'inherit'] })
  const ffDone = once(ff, 'exit')
  let ffFailed = null
  ff.on('exit', (code) => { if (code) ffFailed = new Error(`ffmpeg exited ${code}`) })
  ff.stdin.on('error', (e) => { ffFailed ??= e })

  const bytes = W * H * 4
  let received = 0
  const post = async (req, res) => {
    let got = 0
    for await (const chunk of req) {
      if (ffFailed) throw ffFailed
      got += chunk.length
      if (!ff.stdin.write(chunk)) await once(ff.stdin, 'drain')
    }
    if (got !== bytes) throw new Error(`frame is ${got} bytes, expected ${bytes}`)
    received++
    res.writeHead(204).end()
  }

  const stage = await openStage(values.size, { post })
  const t0 = Date.now()
  try {
    await stage.page.evaluate((b) => window.film.load(b), board)
    console.log(`${frames.length} frames (${(frames.length / cut.fps).toFixed(1)}s) at ${W}×${H} · ${stage.renderer}`)
    for (const [k, n] of frames.entries()) {
      const r = await stage.page.evaluate(([n, url]) => window.film.frame(n, url), [n, `${stage.url}/frame`])
      if (stage.errors.length) throw new Error(`frame ${n}:\n${stage.errors.join('\n')}`)
      if (ffFailed) throw ffFailed
      if ((k + 1) % cut.fps === 0 || k === frames.length - 1) {
        const el = (Date.now() - t0) / 1000, rate = (k + 1) / el
        console.log(`${String(k + 1).padStart(5)}/${frames.length}  ${r.shots.join(' → ').padEnd(28)} ${rate.toFixed(1)} fps  eta ${clock((frames.length - k - 1) / rate)}`)
      }
    }
  } catch (e) {
    ff.kill('SIGKILL')
    for (const o of outputs) rmSync(o.part, { force: true })
    throw e
  } finally {
    await stage.close()
  }
  ff.stdin.end()
  await ffDone
  if (ffFailed || received !== frames.length) {
    for (const o of outputs) rmSync(o.part, { force: true })
    throw ffFailed ?? new Error(`ffmpeg was sent ${received} of ${frames.length} frames`)
  }
  for (const o of outputs) renameSync(o.part, o.path)

  const seconds = (Date.now() - t0) / 1000
  const manifest = {
    storyboard: sbPath, digest, name, partial, fps: cut.fps, frames: frames.length, runtime: frames.length / cut.fps,
    first: frames[0], size: { width: W, height: H }, renderer: stage.renderer,
    outputs: outputs.map(({ tag, w, h, crf, path }) => ({ tag, width: w, height: h, crf, path: relative(outDir, path) })),
    shots: cut.shots.map((r) => ({ id: r.shot.id, f0: r.f0, f1: r.f1, enter: r.shot.enter ?? null, exit: r.shot.exit ?? null })),
    render: { seconds: Math.round(seconds), msPerFrame: Math.round((seconds * 1000) / frames.length) },
  }
  writeFileSync(join(outDir, `${name}.json`), JSON.stringify(manifest, null, 2))
  console.log(`\n${frames.length} frames in ${clock(seconds)} (${manifest.render.msPerFrame} ms/frame)`)
  for (const o of outputs) console.log(`${o.tag}: ${o.path}`)
}

main().catch((e) => { console.error(e.message ?? e); process.exit(1) })
