#!/usr/bin/env node
// provision → pins → tts → record → mux. Every server this starts is torn
// down on the way out, whether the build finished, failed or was interrupted.
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { isAbsolute, join, resolve } from 'node:path'
import { parseArgs } from 'node:util'

import {
  API_BASE, DEFAULT_OUT, DEFAULT_STORYBOARD, liveStores, py, snapshotCounts, tmuxSessions,
} from './lib/env.mjs'
import { CAPTIONS, mux } from './lib/mux.mjs'
import { Stack } from './lib/provision.mjs'
import { record } from './lib/recorder.mjs'
import { synthesize } from './lib/tts.mjs'
import { sceneFns } from './scenes.mjs'

export const OUTPUT_NAME = 'regin-promo-zh'
const STALE = [`${OUTPUT_NAME}.mp4`, `${OUTPUT_NAME}.srt`, CAPTIONS, 'manifest.json', 'frames', 'raw.webm', 'hero', 'text', 'voice']

class PinError extends Error {}

function loadStoryboard(path) {
  const sb = JSON.parse(readFileSync(path, 'utf8'))
  const ids = new Set()
  if (!sb.scenes?.length) throw new Error('storyboard: no scenes')
  for (const scene of sb.scenes) {
    if (!scene.lines?.length) throw new Error(`storyboard: scene "${scene.id}" has no lines`)
    for (const id of [scene.id, ...scene.lines.map((l) => l.id)]) {
      if (ids.has(id)) throw new Error(`storyboard: duplicate id "${id}"`)
      ids.add(id)
    }
    if (scene.surface !== 'card' && !sceneFns[scene.id]) {
      throw new Error(`storyboard: scene "${scene.id}" has no function in scenes.mjs`)
    }
  }
  return sb
}

async function resolvePins(pins, token, snapshotDb) {
  for (const [kind, path] of [['feature', 'features'], ['task', 'tasks'], ['defect', 'defects']]) {
    const res = await fetch(`${API_BASE}/api/${path}/${pins[kind]}`,
      { headers: { authorization: `Bearer ${token}` } })
    if (!res.ok) throw new PinError(`pin ${kind}=${pins[kind]} does not resolve: GET /api/${path}/${pins[kind]} → ${res.status}`)
  }
  if (!pins.sessions?.length) throw new PinError('pin sessions is empty — at least one ended session is required')
  const rows = JSON.parse(py(`
import json, sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
rows = {t: c.execute("select status from sessions where trace_id=?", (t,)).fetchone() for t in sys.argv[2:]}
print(json.dumps({t: (r[0] or "") if r else None for t, r in rows.items()}))
`, [snapshotDb, ...pins.sessions]))
  pins.sessions.forEach((id, i) => {
    if (rows[id] === null) {
      throw new PinError(`pin sessions[${i}]=${id} does not resolve: no such session in the snapshot`)
    }
    if (rows[id] !== 'ended') {
      throw new PinError(`pin sessions[${i}]=${id} is not ended (status ${JSON.stringify(rows[id])}) — a live session cannot be pinned`)
    }
  })
}

async function main() {
  const { values } = parseArgs({ options: { storyboard: { type: 'string' }, out: { type: 'string' } } })
  const sbPath = resolve(values.storyboard ?? DEFAULT_STORYBOARD)
  const outDir = values.out ? (isAbsolute(values.out) ? values.out : resolve(values.out)) : DEFAULT_OUT
  const storyboard = loadStoryboard(sbPath)
  mkdirSync(outDir, { recursive: true })
  for (const f of STALE) rmSync(join(outDir, f), { recursive: true, force: true })

  const live = liveStores()
  const stack = new Stack(outDir, live)
  let tearing = null
  const teardown = () => (tearing ??= stack.teardown())
  process.on('exit', () => stack.killNow())
  for (const sig of ['SIGINT', 'SIGTERM']) {
    process.on(sig, async () => { await teardown(); process.exit(130) })
  }

  let recorded
  const before = {}, after = {}
  try {
    console.error(`[promo] snapshot ${live.trace} → ${stack.paths.traceDb}`)
    stack.snapshot()
    before.counts = snapshotCounts(stack.paths.traceDb)
    before.tmux = tmuxSessions(live.tmux_socket)
    await stack.serve()
    const auth = await stack.login()
    await resolvePins(storyboard.pins, auth.token, stack.paths.traceDb)
    console.error('[promo] pins resolved; synthesizing narration')
    const durations = synthesize(storyboard, outDir)
    console.error('[promo] recording')
    recorded = await record({ storyboard, sceneFns, durations, outDir, auth: { token: auth.token, user: auth.user } })
  } finally {
    await teardown()
  }
  after.counts = snapshotCounts(stack.paths.traceDb)
  after.tmux = tmuxSessions(live.tmux_socket)

  const manifest = {
    storyboard: sbPath,
    pins: storyboard.pins,
    ...recorded,
    snapshot_counts: { before: before.counts, after: after.counts },
    tmux_sessions: { before: before.tmux, after: after.tmux },
    stores: { snapshot: stack.paths.traceDb, live: live.trace, tmux_socket: live.tmux_socket },
  }
  console.error('[promo] muxing')
  const { mp4, srt } = mux({ manifest, storyboard, outDir, name: OUTPUT_NAME })
  writeFileSync(join(outDir, 'manifest.json'), JSON.stringify(manifest, null, 2))
  console.error(`[promo] wrote ${mp4}\n[promo] wrote ${srt}`)
}

main().then(() => process.exit(0), (err) => {
  console.error(err instanceof PinError ? `[promo] REFUSED — ${err.message}` : `[promo] FAILED — ${err.stack ?? err}`)
  process.exit(1)
})
