import { execFileSync } from 'node:child_process'
import { mkdirSync, rmSync } from 'node:fs'
import { join } from 'node:path'

import { ffprobeDuration } from './env.mjs'

// `say` pads each clip with silence at both ends. Trimmed, a clip's start is
// its speech onset, which is what the subtitle cue is held against (R5).
const TRIM = 'silenceremove=start_periods=1:start_threshold=-50dB,' +
  'areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse'

export function synthesize(storyboard, outDir) {
  const dir = join(outDir, 'voice')
  mkdirSync(dir, { recursive: true })
  const voice = storyboard.voice || 'Tingting'
  const rate = storyboard.rate ? ['-r', String(storyboard.rate)] : []
  const durations = {}
  for (const scene of storyboard.scenes) {
    for (const line of scene.lines) {
      const raw = join(dir, `${line.id}.raw.aiff`)
      const clip = join(dir, `${line.id}.aiff`)
      execFileSync('say', ['-v', voice, ...rate, '-o', raw, '--', line.speak ?? line.text])
      execFileSync('ffmpeg', ['-v', 'error', '-y', '-i', raw, '-af', TRIM, clip])
      rmSync(raw)
      durations[line.id] = ffprobeDuration(clip)
      if (!(durations[line.id] > 0)) throw new Error(`line ${line.id}: TTS produced no audio`)
    }
  }
  return durations
}
