import { execFileSync, spawnSync } from 'node:child_process'
import { rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { FRAME } from './env.mjs'

const LOUDNESS = { I: -16, TP: -1.5, LRA: 11 }

const stamp = (s) => {
  const ms = Math.round(s * 1000)
  const p = (n, w = 2) => String(n).padStart(w, '0')
  return `${p(Math.floor(ms / 3600000))}:${p(Math.floor(ms / 60000) % 60)}:${p(Math.floor(ms / 1000) % 60)},${p(ms % 1000, 3)}`
}

export function placedLines(manifest, storyboard) {
  const text = Object.fromEntries(storyboard.scenes.flatMap((s) => s.lines.map((l) => [l.id, l.text])))
  return manifest.scenes.flatMap((s) => s.lines.map((l) => ({ ...l, text: text[l.id] })))
}

export function writeSrt(lines, file) {
  writeFileSync(file, lines.map((l, i) =>
    `${i + 1}\n${stamp(l.start_s)} --> ${stamp(l.start_s + l.dur_s)}\n${l.text}\n`).join('\n'))
}

function mixVoice(lines, outDir, total, file) {
  const inputs = lines.flatMap((l) => ['-i', join(outDir, 'voice', `${l.id}.aiff`)])
  const delays = lines.map((l, i) => `[${i}]adelay=${Math.round(l.start_s * 1000)}:all=1[d${i}]`)
  const mix = `${lines.map((_, i) => `[d${i}]`).join('')}amix=inputs=${lines.length}:normalize=0:duration=longest,` +
    `apad,atrim=0:${total.toFixed(3)},aresample=48000[a]`
  execFileSync('ffmpeg', ['-v', 'error', '-y', ...inputs, '-filter_complex', [...delays, mix].join(';'),
    '-map', '[a]', '-c:a', 'pcm_s16le', file])
}

function loudnessJson(file) {
  const r = spawnSync('ffmpeg', ['-hide_banner', '-nostats', '-i', file, '-af',
    `loudnorm=I=${LOUDNESS.I}:TP=${LOUDNESS.TP}:LRA=${LOUDNESS.LRA}:print_format=json`, '-f', 'null', '-'],
  { encoding: 'utf8' })
  if (r.status !== 0) throw new Error(`loudnorm measure failed:\n${r.stderr}`)
  return JSON.parse(r.stderr.slice(r.stderr.lastIndexOf('{'), r.stderr.lastIndexOf('}') + 1))
}

// The subtitles are drawn into every frame, so the mp4 carries no subtitle
// track: a player shows a soft track on top of the burned-in one, and the
// viewer reads every line twice. For the same reason the SRT must not share
// the mp4's name — players auto-load a sidecar that does.
export const CAPTIONS = 'captions.srt'

export function mux({ manifest, storyboard, outDir, name }) {
  const lines = placedLines(manifest, storyboard)
  const total = manifest.scenes.at(-1).end_s
  const srt = join(outDir, CAPTIONS), mp4 = join(outDir, `${name}.mp4`)
  const voice = join(outDir, 'voice.wav')
  writeSrt(lines, srt)
  mixVoice(lines, outDir, total, voice)
  // Two-pass loudnorm: the first pass measures, the second applies a linear
  // gain. Single-pass runs dynamic compression and lands off target on a
  // track that is mostly silence between lines.
  const m = loudnessJson(voice)
  const norm = `loudnorm=I=${LOUDNESS.I}:TP=${LOUDNESS.TP}:LRA=${LOUDNESS.LRA}:` +
    `measured_I=${m.input_i}:measured_TP=${m.input_tp}:measured_LRA=${m.input_lra}:` +
    `measured_thresh=${m.input_thresh}:offset=${m.target_offset}:linear=true,aresample=48000`
  execFileSync('ffmpeg', ['-v', 'error', '-y',
    '-f', 'concat', '-safe', '0', '-i', join(outDir, 'frames', 'frames.ffconcat'), '-i', voice,
    '-filter_complex', `[0:v]fps=30,scale=${FRAME.width}:${FRAME.height}:flags=lanczos,format=yuv420p[v];[1:a]${norm}[a]`,
    '-map', '[v]', '-map', '[a]', '-sn',
    '-t', total.toFixed(3),
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '16', '-profile:v', 'high', '-level', '5.1',
    '-r', '30', '-fps_mode', 'cfr',
    '-c:a', 'aac', '-b:a', '192k',
    '-movflags', '+faststart', mp4])
  rmSync(voice)
  return { mp4, srt }
}
