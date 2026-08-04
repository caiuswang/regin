#!/usr/bin/env node
/**
 * Provision the scratch DB, then become `regin serve` on the E2E port.
 *
 * Provisioning lives here rather than in `globalSetup` because Playwright's
 * ordering between `webServer` and `globalSetup` has flipped between releases,
 * and `regin serve` cannot boot against a database that does not exist yet.
 * Making the server responsible for its own store removes the ordering question
 * entirely.
 *
 * The wipe is unconditional: a run killed before `global-teardown.js` leaves a
 * populated scratch dir behind, and inheriting it would reintroduce exactly the
 * cross-run accumulation this replaces.
 */
import { execFileSync, spawn } from 'node:child_process'
import { copyFileSync, mkdirSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { API_PORT, SCRATCH_DIR, TEST_USER, applyScratchEnv } from '../e2e-env.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')
const CLI = resolve(ROOT, 'cli/regin.py')

const env = { ...process.env, ...applyScratchEnv() }

rmSync(SCRATCH_DIR, { recursive: true, force: true })
mkdirSync(join(SCRATCH_DIR, 'db'), { recursive: true })
// `regin init` creates the data dir but not the activity log's, and loguru
// only reports the miss on every request via a stderr traceback.
mkdirSync(join(SCRATCH_DIR, 'data', 'logs'), { recursive: true })
// The committed `settings.json` comes along (specs rely on the configured rule
// engines); `settings.local.json` deliberately does not — machine-specific
// overrides are exactly the ambient input the suite should not inherit. Writes
// from the settings API land on this copy.
mkdirSync(join(SCRATCH_DIR, 'config'), { recursive: true })
copyFileSync(resolve(ROOT, 'config/settings.json'),
  join(SCRATCH_DIR, 'config', 'settings.json'))

const run = (args) =>
  execFileSync(PY, [CLI, ...args], { cwd: ROOT, env, encoding: 'utf8' })

run(['init'])
run(['users', 'create', TEST_USER.username, TEST_USER.password, '--role', 'admin'])
// The repo/topics/proposal specs navigate to `/repos` and click the first row.
// They used to land on whatever the developer happened to have registered.
execFileSync(PY, [resolve(ROOT, 'frontend/scripts/e2e-seed.py')],
  { cwd: ROOT, env, encoding: 'utf8' })

const server = spawn(PY, [CLI, 'serve', '--port', String(API_PORT)], {
  cwd: ROOT, env, stdio: 'inherit',
})
server.on('exit', (code) => process.exit(code ?? 0))
for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => server.kill(sig))
}
