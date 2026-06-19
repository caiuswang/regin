#!/usr/bin/env node
/**
 * dom-measure — drive an authed regin page and measure real DOM geometry.
 *
 * Purpose: kill the trial-and-error Playwright loop for *surface bugs*
 * (CSS / layout / overlap / sizing). It mints a valid JWT, navigates to a
 * route, runs an explicit sequence of "reveal" steps to expose the element
 * (click a tab, open a <details>, …), then prints `getBoundingClientRect`
 * for the selectors you care about plus a pairwise overlap/gap report — so
 * you diagnose and verify with hard pixel numbers, not by eyeballing a
 * screenshot. See `.claude/skills/surface-bug/content.md`.
 *
 * All paths are derived from this file's location, so the script is portable.
 *
 * Usage:
 *   node scripts/dom-measure.mjs --route /memory \
 *     --reveal 'click:button:has-text("Topics")' \
 *     --reveal 'open-details' \
 *     --rect 'th:has-text("When")' \
 *     --rect 'button[aria-label*="up" i]' \
 *     [--shot .nvim/after.png]
 *
 * Flags:
 *   --route <path>        route appended to --base (default "/")
 *   --base <url>          origin (default http://localhost:5173 — vite dev)
 *   --token <jwt>         use this JWT instead of minting one
 *   --user/-u <id>        user id to mint for (default 1)
 *   --username <name>     username claim (default "pw")
 *   --role <role>         role claim (default "editor")
 *   --reveal <spec>       repeatable, applied in order. Specs:
 *                           click:<selector>      click first match
 *                           open-details          set every <details>.open = true
 *                           open-details:<sel>    open <details> matching <sel>
 *                           wait:<ms>             sleep
 *                           text:<string>         scroll the text into view
 *                           eval:<js>             run arbitrary JS in the page
 *   --rect <selector>     repeatable; measure first match of each
 *   --shot <path>         also write a full-page screenshot here
 *   --viewport <WxH>      viewport (default 1280x1100)
 *   --timeout <ms>        per-step timeout (default 5000)
 *   --headed              run with a visible browser (debugging)
 */

import { spawnSync } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

// ---- arg parsing (supports repeatable --reveal / --rect) -------------------
function parseArgs(argv) {
  const opts = {
    route: '/', base: 'http://localhost:5173', token: null,
    user: '1', username: 'pw', role: 'editor',
    reveal: [], rect: [], shot: null, viewport: '1280x1100',
    timeout: 5000, headed: false,
  }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    const next = () => argv[++i]
    switch (a) {
      case '--route': opts.route = next(); break
      case '--base': opts.base = next(); break
      case '--token': opts.token = next(); break
      case '--user': case '-u': opts.user = next(); break
      case '--username': opts.username = next(); break
      case '--role': opts.role = next(); break
      case '--reveal': opts.reveal.push(next()); break
      case '--rect': opts.rect.push(next()); break
      case '--shot': opts.shot = next(); break
      case '--viewport': opts.viewport = next(); break
      case '--timeout': opts.timeout = Number(next()); break
      case '--headed': opts.headed = true; break
      default:
        console.error(`unknown flag: ${a}`)
        process.exit(2)
    }
  }
  return opts
}

const opts = parseArgs(process.argv.slice(2))

// ---- mint a JWT via the project's own auth code ----------------------------
function mintToken() {
  if (opts.token) return opts.token
  const py = join(ROOT, '.venv', 'bin', 'python')
  const code =
    'from lib.auth import create_token;' +
    `print(create_token(${Number(opts.user)}, ${JSON.stringify(opts.username)}, ${JSON.stringify(opts.role)}))`
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
const [vw, vh] = opts.viewport.split('x').map(Number)

const browser = await chromium.launch({ headless: !opts.headed })
const page = await browser.newPage({ viewport: { width: vw, height: vh } })

// inject the auth token before any page script runs (Vue router guard only
// checks the key exists; the running server verifies the signature)
await page.addInitScript((t) => {
  try { localStorage.setItem('regin_auth_token', t) } catch {}
}, token)

const warnings = []
const url = opts.base.replace(/\/$/, '') + opts.route
await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 })
await page.waitForTimeout(500) // initial Vue mount settle

// ---- apply reveal steps in order, never hang on one --------------------------
for (const spec of opts.reveal) {
  const ci = spec.indexOf(':')
  const verb = ci === -1 ? spec : spec.slice(0, ci)
  const arg = ci === -1 ? '' : spec.slice(ci + 1)
  try {
    if (verb === 'click') {
      await page.locator(arg).first().click({ timeout: opts.timeout })
    } else if (verb === 'open-details') {
      await page.evaluate((sel) => {
        const nodes = sel ? document.querySelectorAll(sel) : document.querySelectorAll('details')
        nodes.forEach((d) => { d.open = true })
      }, arg || null)
    } else if (verb === 'wait') {
      await page.waitForTimeout(Number(arg))
    } else if (verb === 'text') {
      await page.getByText(arg, { exact: false }).first()
        .scrollIntoViewIfNeeded({ timeout: opts.timeout })
    } else if (verb === 'eval') {
      await page.evaluate(arg)
    } else {
      warnings.push(`unknown reveal verb: ${verb}`)
      continue
    }
    await page.waitForTimeout(150)
  } catch (e) {
    warnings.push(`reveal "${spec}" failed: ${e.message.split('\n')[0]}`)
  }
}

// ---- measure rects (locators support both CSS and :has-text pseudos) --------
const rects = []
for (const sel of opts.rect) {
  const loc = page.locator(sel).first()
  let box = null, text = ''
  try {
    box = await loc.boundingBox({ timeout: opts.timeout })
    text = ((await loc.textContent({ timeout: opts.timeout })) || '').trim().slice(0, 40)
  } catch (e) {
    warnings.push(`rect "${sel}" not found/visible: ${e.message.split('\n')[0]}`)
  }
  if (!box) { rects.push({ selector: sel, found: false }); continue }
  // overflow probe: scrollWidth > clientWidth means content is wider than the
  // (possibly fixed-width) box and is spilling — the classic table-fixed bug.
  let overflow = null
  try {
    overflow = await loc.evaluate((el) => ({
      scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
      overflowingX: el.scrollWidth > el.clientWidth + 1,
    }))
  } catch { /* element detached; skip overflow probe */ }
  const round = (n) => +n.toFixed(1)
  rects.push({
    selector: sel, found: true,
    left: round(box.x), right: round(box.x + box.width),
    top: round(box.y), bottom: round(box.y + box.height),
    width: round(box.width), height: round(box.height),
    text, ...(overflow || {}),
  })
}

// ---- pairwise horizontal gap / overlap between consecutive rects ------------
const pairs = []
for (let i = 0; i + 1 < rects.length; i++) {
  const a = rects[i], b = rects[i + 1]
  if (!a.found || !b.found) continue
  // order left→right so the report reads naturally regardless of arg order
  const [l, r] = a.left <= b.left ? [a, b] : [b, a]
  const gap = +(r.left - l.right).toFixed(1)
  pairs.push({
    left: l.selector, right: r.selector,
    gap, overlap: gap < 0 ? +(-gap).toFixed(1) : 0,
    verdict: gap < 0 ? `OVERLAP by ${(-gap).toFixed(1)}px` : `clear by ${gap.toFixed(1)}px`,
  })
}

if (opts.shot) {
  await page.screenshot({ path: opts.shot, fullPage: true })
}

await browser.close()

console.log(JSON.stringify({ url, rects, pairs, warnings, shot: opts.shot || null }, null, 2))
