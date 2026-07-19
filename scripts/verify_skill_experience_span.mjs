#!/usr/bin/env node
/**
 * verify_skill_experience_span — prove the injected `<skill_experience>`
 * block renders in the live session-trace UI.
 *
 * Background: when a skill is invoked (the `Skill` tool, or a `/skill`
 * slash command), regin injects a `<skill_experience>` block of past-session
 * lessons. That inject must ALSO surface in the trace detail — it does so as
 * a `memory.recall` span marked `attributes.source = 'skill_experience'`,
 * which the conversation view renders via `MemoryRecallRow.vue` with an
 * emerald "skill experience" label + a skill-id chip. This script drives the
 * authed SPA headlessly and asserts that row is present for a given session,
 * the regression net for "the inject happened but nothing showed in the trace".
 *
 * All paths are derived from this file's location, so the script is portable.
 *
 * Usage:
 *   # self-contained: seed a fresh minimal trace, then verify it renders
 *   node scripts/verify_skill_experience_span.mjs --seed [--shot out.png]
 *   # or verify a specific existing session
 *   node scripts/verify_skill_experience_span.mjs <trace_id> \
 *     [--base http://localhost:8321] [--skill <id>] [--shot out.png] [--headed]
 *
 * Exits 0 when the skill-experience row is found, 1 otherwise — CI-usable.
 * Requires `regin serve` running on --base and the frontend dist built
 * (`cd frontend && npx vite build`).
 */
import { spawnSync } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

function parseArgs(argv) {
  const opts = { base: 'http://localhost:8321', headed: false, sid: '', skill: '', shot: '', seed: false }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--base') opts.base = argv[++i]
    else if (a === '--skill') opts.skill = argv[++i]
    else if (a === '--shot') opts.shot = argv[++i]
    else if (a === '--headed') opts.headed = true
    else if (a === '--seed') opts.seed = true
    else if (!a.startsWith('--') && !opts.sid) opts.sid = a
  }
  // --seed creates its own fixed trace; otherwise a trace_id is required.
  if (opts.seed && !opts.sid) { opts.sid = 'verify-skill-experience'; opts.skill = opts.skill || 'playwright-skill' }
  if (!opts.sid) {
    console.error('usage: node scripts/verify_skill_experience_span.mjs (<trace_id>|--seed) [--base url] [--skill id] [--shot png]')
    process.exit(2)
  }
  return opts
}

const opts = parseArgs(process.argv.slice(2))

// ---- optional: seed a fresh minimal trace via the project's ingest path ----
function seedTrace() {
  const py = join(ROOT, '.venv', 'bin', 'python')
  const r = spawnSync(py, [join(ROOT, 'scripts', '_seed_skill_experience_trace.py'), opts.sid],
    { cwd: ROOT, encoding: 'utf8' })
  if (r.status !== 0) {
    console.error('seed failed:\n' + (r.stderr || r.stdout || '(no output)'))
    process.exit(1)
  }
}
if (opts.seed) seedTrace()

// ---- mint a JWT via the project's own auth code ----------------------------
function mintToken() {
  const py = join(ROOT, '.venv', 'bin', 'python')
  const code =
    'from lib.auth import create_token;' +
    'print(create_token(1, "verify-skill-exp", "admin"))'
  const r = spawnSync(py, ['-c', code], { cwd: ROOT, encoding: 'utf8' })
  if (r.status !== 0) {
    console.error('token mint failed:\n' + (r.stderr || r.stdout || '(no output)'))
    process.exit(1)
  }
  return r.stdout.trim()
}

// ---- playwright (from the frontend's own install) --------------------------
const pwUrl = pathToFileURL(join(ROOT, 'frontend', 'node_modules', 'playwright', 'index.mjs'))
const { chromium } = await import(pwUrl.href)

const token = mintToken()
const browser = await chromium.launch({ headless: !opts.headed })
const page = await browser.newPage({ viewport: { width: 1100, height: 800 } })
const errors = []
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))

try {
  // 1. auth — inject the JWT at the origin BEFORE hitting a guarded route,
  //    else the SPA bounces to /login and renders a blank pane.
  await page.goto(opts.base + '/')
  await page.evaluate((tok) => {
    localStorage.setItem('regin_auth_token', tok)
    localStorage.setItem('regin_auth_user',
      JSON.stringify({ id: 1, username: 'verify-skill-exp', role: 'admin' }))
  }, token)

  // 2. open the trace, conversation view (where MemoryRecallRow renders)
  await page.goto(`${opts.base}/trace/sessions/${opts.sid}?view=conversation`,
    { waitUntil: 'load' })
  await page.waitForTimeout(2000)

  // 3. assert the skill-experience row rendered
  const label = page.locator('span', { hasText: /^skill experience$/ }).first()
  const found = await label.count()
  if (!found) {
    console.error(`FAIL: no "skill experience" row in trace ${opts.sid}`)
    console.error('  (is regin serve up, the dist rebuilt, and does this session have a skill_experience inject?)')
    process.exit(1)
  }
  await label.scrollIntoViewIfNeeded()
  const row = label.locator('xpath=ancestor::div[contains(@class,"pl-3")][1]')
  const header = ((await row.locator('div').first().textContent()) || '')
    .replace(/\s+/g, ' ').trim()

  // 4. expand the block to confirm the actual <skill_experience> text is shown
  const toggle = row.getByRole('button')
  let blockHead = ''
  if (await toggle.count()) {
    await toggle.first().click()
    await page.waitForTimeout(300)
    blockHead = ((await row.locator('pre').first().textContent().catch(() => '')) || '').slice(0, 80)
  }

  const skillOk = !opts.skill || header.includes(opts.skill)
  const blockOk = blockHead.includes('<skill_experience>')
  console.log(JSON.stringify({
    trace_id: opts.sid,
    rowHeader: header.slice(0, 160),
    blockHead,
    skillMatch: skillOk,
    blockRendered: blockOk,
    pageErrors: errors,
  }, null, 2))

  if (opts.shot) {
    await row.screenshot({ path: opts.shot }).catch(() => page.screenshot({ path: opts.shot }))
    console.log('screenshot: ' + opts.shot)
  }

  if (!skillOk || !blockOk || errors.length) {
    console.error('FAIL: row present but assertions did not all pass')
    process.exit(1)
  }
  console.log('PASS: <skill_experience> inject renders in the trace detail')
} finally {
  await browser.close()
}
