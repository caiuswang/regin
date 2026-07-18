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
 *   --explain <selector>  repeatable; full diagnosis of the first match —
 *                         computed styles, the ancestor chain, and which
 *                         ancestor clips / scrolls / opens a stacking
 *                         context. Use when a rect alone doesn't explain
 *                         the bug ("z-index does nothing", "it's cut off",
 *                         "position:fixed anchors to the wrong box").
 *   --nth <i>             explain the i-th match instead of the first
 *   --tokens [substr]     dump resolved CSS custom properties (optionally
 *                         filtered by substring) as they compute at runtime
 *   --shot <path>         also write a full-page screenshot here
 *   --viewport <WxH>      viewport (default 1280x1100)
 *   --timeout <ms>        per-step timeout (default 5000)
 *   --headed              run with a visible browser (debugging)
 */

import { spawnSync } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve, join } from 'node:path'

import { explainElement, resolveTokens } from './lib/dom-introspect.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

// ---- arg parsing (supports repeatable --reveal / --rect) -------------------
function parseArgs(argv) {
  const opts = {
    route: '/', base: 'http://localhost:5173', token: null,
    user: '1', username: 'pw', role: 'editor',
    reveal: [], rect: [], explain: [], shot: null, viewport: '1280x1100',
    timeout: 5000, headed: false, tokens: null, nth: null,
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
      case '--explain': opts.explain.push(next()); break
      case '--nth': opts.nth = Number(next()); break
      // Optional value: a bare --tokens dumps everything, so only consume
      // the next argv entry when it isn't another flag.
      case '--tokens':
        opts.tokens = (argv[i + 1] && !argv[i + 1].startsWith('--')) ? next() : ''
        break
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

// ---- explain: computed styles + ancestor chain for one element -------------
const explained = []
for (const sel of opts.explain) {
  try {
    const all = page.locator(sel)
    await all.first().waitFor({ state: 'attached', timeout: opts.timeout })
    const matchCount = await all.count()
    const index = opts.nth ?? 0
    const out = await all.nth(index).evaluate(explainElement, { maxDepth: 12 })
    out.selector = sel
    out.matchCount = matchCount
    out.index = index

    // A selector's first DOM match is often a hidden one (an unmounted tab,
    // a template row) while the element the user can actually see sits
    // later. Without this, the agent concludes "it's hidden" and starts
    // hunting for a reveal step that was never needed.
    if (out.rendered === false && matchCount > 1) {
      const limit = Math.min(matchCount, 20)
      for (let i = 0; i < limit; i++) {
        if (i === index) continue
        const box = await all.nth(i).boundingBox().catch(() => null)
        if (box && box.width > 0) {
          out.hint = `match #${index} is not rendered, but match #${i} is `
                   + `(${Math.round(box.width)}×${Math.round(box.height)} at `
                   + `${Math.round(box.x)},${Math.round(box.y)}). `
                   + `Re-run with --nth ${i}, or use a more specific selector.`
          break
        }
      }
      if (!out.hint) {
        out.hint = `none of the first ${limit} matches are rendered — the `
                 + `whole subtree is hidden, so the missing reveal step is a `
                 + `tab/panel above it, not this element.`
      }
    }
    explained.push(out)
  } catch (e) {
    warnings.push(`explain "${sel}" failed: ${e.message.split('\n')[0]}`)
    explained.push({ selector: sel, found: false })
  }
}

// ---- resolved design tokens ------------------------------------------------
let tokens = null
if (opts.tokens !== null) {
  try {
    tokens = await page.evaluate(resolveTokens, opts.tokens || null)
  } catch (e) {
    warnings.push(`tokens failed: ${e.message.split('\n')[0]}`)
  }
}

if (opts.shot) {
  await page.screenshot({ path: opts.shot, fullPage: true })
}

await browser.close()

const payload = { url, rects, pairs, warnings, shot: opts.shot || null }
if (opts.explain.length) payload.explained = explained
if (tokens) payload.tokens = tokens
console.log(JSON.stringify(payload, null, 2))
