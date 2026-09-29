import { execFileSync, spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { copyFileSync, mkdirSync, rmSync } from 'node:fs'
import { connect } from 'node:net'
import { join } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'

import {
  API_PORT, CLI, FRONTEND, PROMO_USER, PY, ROOT, VITE_PORT, API_BASE, APP_BASE,
  py, scratchEnv, scratchPaths,
} from './env.mjs'

// Connect, don't bind: Node listens with SO_REUSEADDR, so a trial bind on
// the wildcard succeeds beside a server already holding 127.0.0.1:<port>.
async function assertPortFree(port) {
  for (const host of ['127.0.0.1', '::1']) {
    const taken = await new Promise((ok) => {
      const sock = connect({ port, host })
      sock.once('connect', () => { sock.destroy(); ok(true) })
      sock.once('error', () => ok(false))
    })
    if (taken) throw new Error(`port ${port} is already in use on ${host} — another promo build or a stray server holds it`)
  }
}

// SQLite's online backup: a consistent copy of a DB that live writers keep
// appending to, which a file copy of the db + wal pair is not.
function backup(src, dst) {
  py(`
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
`, [src, dst])
}

// A readiness probe, as Playwright's own `webServer` does: neither server's
// startup banner is a reliable signal once its stdout is a pipe (Python
// block-buffers it). The probe gives up the moment the process exits.
async function waitReady(child, url, label, deadlineMs = 120000) {
  let exited = null
  child.once('exit', (code) => { exited = code ?? 'signal' })
  const until = Date.now() + deadlineMs
  while (exited === null && Date.now() < until) {
    try {
      const res = await fetch(url)
      if (res.status < 500) return
    } catch { /* not listening yet */ }
    await sleep(250)
  }
  throw new Error(exited !== null ? `${label} exited (${exited}) before it was ready`
    : `${label} not ready at ${url} after ${deadlineMs / 1000}s`)
}

export class Stack {
  constructor(outDir, live) {
    this.paths = scratchPaths(outDir)
    this.live = live
    this.env = { ...process.env, ...scratchEnv(this.paths) }
    this.children = []
    this.password = randomBytes(12).toString('hex')
  }

  snapshot() {
    const p = this.paths
    rmSync(p.scratch, { recursive: true, force: true })
    mkdirSync(join(p.scratch, 'db'), { recursive: true })
    mkdirSync(join(p.dataDir, 'logs'), { recursive: true })
    mkdirSync(p.configDir, { recursive: true })
    copyFileSync(join(ROOT, 'config/settings.json'), join(p.configDir, 'settings.json'))
    backup(this.live.trace, p.traceDb)
    backup(this.live.memory, p.memoryDb)
    execFileSync(PY, [CLI, 'users', 'create', PROMO_USER, this.password, '--role', 'admin'],
      { cwd: ROOT, env: this.env, stdio: ['ignore', 'ignore', 'inherit'] })
  }

  async serve() {
    await assertPortFree(API_PORT)
    await assertPortFree(VITE_PORT)
    const api = this.spawn(PY, [CLI, 'serve', '--replica', '--port', String(API_PORT)], ROOT, this.env)
    await waitReady(api, `${API_BASE}/api/auth/me`, 'regin serve --replica')
    const vite = this.spawn(process.execPath,
      [join(FRONTEND, 'node_modules/vite/bin/vite.js'), '--port', String(VITE_PORT), '--strictPort'],
      FRONTEND, { ...this.env, REGIN_API_TARGET: API_BASE })
    await waitReady(vite, `${APP_BASE}/`, 'vite')
  }

  spawn(cmd, args, cwd, env) {
    // Own process group, so teardown reaches the grandchildren (vite's esbuild,
    // werkzeug's reloader) and not only the direct child.
    const child = spawn(cmd, args, { cwd, env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] })
    child.stdout.resume(); child.stderr.resume()
    this.children.push(child)
    return child
  }

  async login() {
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ username: PROMO_USER, password: this.password }),
    })
    if (!res.ok) throw new Error(`promo-admin login failed: ${res.status}`)
    return res.json()
  }

  async teardown() {
    const running = this.children.filter((c) => c.exitCode === null && c.signalCode === null)
    const exited = running.map((c) => new Promise((ok) => c.once('exit', ok)))
    for (const child of running) signalGroup(child, 'SIGTERM')
    await Promise.all(exited)
    this.children = []
  }

  // For `process.on('exit')`, where nothing can be awaited.
  killNow() {
    for (const child of this.children) signalGroup(child, 'SIGKILL')
  }
}

function signalGroup(child, sig) {
  try { process.kill(-child.pid, sig) } catch { /* group already gone */ }
}
