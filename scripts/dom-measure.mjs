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
 *   --rules <selector>    repeatable; which CSS rule actually WON for each
 *                         layout property, with source file:line and the
 *                         declarations it beat (CDP getMatchedStylesForNode)
 *   --rules-props <list>  comma-separated properties for --rules, or "all"
 *   --overlaps <selector> repeatable; sweep a table/container across ALL rows
 *                         for text spill, child overlap, sticky-header drift
 *                         and squished columns
 *   --overflow            does the app content pane scroll sideways? Reports
 *                         `.content-scroll` scrollWidth vs clientWidth, names
 *                         the offending descendants, and flags columns
 *                         squished to a per-character sliver. Same detectors
 *                         responsive.spec.js gates on. Measure this, NOT
 *                         documentElement — `.content` sets overflow-x:hidden
 *                         above the pane, so the document never scrolls and a
 *                         documentElement check passes on a broken page.
 *   --baseline [ref]      measure twice — once as the tree stands, once with
 *                         frontend/src checked out at <ref> (default HEAD) —
 *                         and print the deltas. The before/after for an
 *                         uncommitted fix in one command. Working-tree changes
 *                         are snapshotted via `git stash create` and restored
 *                         after; the sha is printed so a crash is recoverable.
 *   --baseline-wait <ms>  vite HMR settle time after the swap (default 1500)
 *   --tokens [substr]     dump resolved CSS custom properties (optionally
 *                         filtered by substring) as they compute at runtime
 *   --shot <path>         also write a full-page screenshot here
 *   --viewport <WxH>      viewport (default 1280x1100)
 *   --timeout <ms>        per-step timeout (default 5000)
 *   --headed              run with a visible browser (debugging)
 */

import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve, join } from 'node:path'

import { explainElement, resolveTokens, scanOverlaps } from './lib/dom-introspect.mjs'
import { matchedRules } from './lib/matched-rules.mjs'
// The SAME detectors responsive.spec.js asserts on, not a second copy: a
// measurement that disagrees with the gate is worse than no measurement.
import { contentOverflow, squishedColumns } from '../frontend/tests/helpers/overflow.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

// --help prints this file's own header comment rather than a second copy of
// the flag list — one place to edit, so the two can never disagree.
function printUsage() {
  const src = readFileSync(fileURLToPath(import.meta.url), 'utf8')
  const doc = src.slice(src.indexOf('/**') + 3, src.indexOf('*/'))
  console.log(doc.split('\n').map((l) => l.replace(/^\s*\* ?/, '')).join('\n').trim())
}

// ---- arg parsing (supports repeatable --reveal / --rect) -------------------
function parseArgs(argv) {
  const opts = {
    route: '/', base: 'http://localhost:5173', token: null,
    // admin by default: the router gates every /trace/* route on
    // `regin_auth_user.role === 'admin'` and silently redirects everyone
    // else to the dashboard, so a lower role makes the whole trace surface
    // unmeasurable — and looks like the element is missing, not blocked.
    user: '1', username: 'pw', role: 'admin',
    reveal: [], rect: [], explain: [], shot: null, viewport: '1280x1100',
    timeout: 5000, headed: false, tokens: null, nth: null,
    rules: [], rulesProps: null, overlaps: [],
    overflow: false, baseline: null, baselineWait: 1500,
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
      case '--rules': opts.rules.push(next()); break
      case '--rules-props': opts.rulesProps = next(); break
      case '--overlaps': opts.overlaps.push(next()); break
      case '--overflow': opts.overflow = true; break
      // Optional value: bare --baseline means HEAD (the common "before/after
      // my uncommitted edit" case).
      case '--baseline':
        opts.baseline = (argv[i + 1] && !argv[i + 1].startsWith('--')) ? next() : 'HEAD'
        break
      case '--baseline-wait': opts.baselineWait = Number(next()); break
      // Optional value: a bare --tokens dumps everything, so only consume
      // the next argv entry when it isn't another flag.
      case '--tokens':
        opts.tokens = (argv[i + 1] && !argv[i + 1].startsWith('--')) ? next() : ''
        break
      case '--shot': opts.shot = next(); break
      case '--viewport': opts.viewport = next(); break
      case '--timeout': opts.timeout = Number(next()); break
      case '--headed': opts.headed = true; break
      case '--help': case '-h': printUsage(); process.exit(0)
      default:
        console.error(`unknown flag: ${a}\n`)
        printUsage()
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
const url = opts.base.replace(/\/$/, '') + opts.route

// One full measurement pass. Factored out of the top level so `--baseline`
// can run it twice against two states of the source tree; `shotPath` differs
// per pass so the two screenshots don't overwrite each other.
async function measurePass(shotPath) {
const page = await browser.newPage({ viewport: { width: vw, height: vh } })

// Inject auth before any page script runs. Two keys are required, not one:
// the guard checks `regin_auth_token` exists, but /trace/* additionally
// reads the role off `regin_auth_user` and bounces non-admins to the
// dashboard. Injecting only the token made every trace route measure the
// dashboard instead — indistinguishable from "the element isn't there".
await page.addInitScript(([t, u]) => {
  try {
    localStorage.setItem('regin_auth_token', t)
    localStorage.setItem('regin_auth_user', u)
  } catch { /* storage unavailable; the guard will bounce to /login */ }
}, [token, JSON.stringify({
  id: Number(opts.user), username: opts.username, role: opts.role,
})])

const warnings = []
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

// ---- which CSS rule won -----------------------------------------------------
const ruleReports = []
for (const sel of opts.rules) {
  try {
    const props = opts.rulesProps
      ? (opts.rulesProps === 'all' ? 'all' : opts.rulesProps.split(',').map((p) => p.trim()))
      : null
    ruleReports.push(await matchedRules(page, sel, { props }))
  } catch (e) {
    warnings.push(`rules "${sel}" failed: ${e.message.split('\n')[0]}`)
  }
}

// ---- overlap sweep ----------------------------------------------------------
const overlapReports = []
for (const sel of opts.overlaps) {
  try {
    const loc = page.locator(sel).first()
    await loc.waitFor({ state: 'attached', timeout: opts.timeout })
    overlapReports.push({ selector: sel, ...(await loc.evaluate(scanOverlaps, { maxRows: 400 })) })
  } catch (e) {
    warnings.push(`overlaps "${sel}" failed: ${e.message.split('\n')[0]}`)
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

// ---- app-pane horizontal overflow ------------------------------------------
// Measured on `.content-scroll`, NOT documentElement: AppLayout sets
// overflow-x:hidden on the `.content` wrapper above it, so the document never
// scrolls sideways and `documentElement.scrollWidth <= clientWidth` holds even
// on a visibly broken page. Asserting the document is a vacuous check.
let overflow = null
if (opts.overflow) {
  try {
    overflow = { ...(await contentOverflow(page)), squished: await squishedColumns(page) }
    overflow.overflowsBy = Math.max(0, overflow.scrollWidth - overflow.clientWidth)
    overflow.verdict = !overflow.pane
      ? 'no .content-scroll pane on this route'
      : overflow.overflowsBy > 1
        ? `OVERFLOWS by ${overflow.overflowsBy}px`
        : 'clean'
  } catch (e) {
    warnings.push(`overflow failed: ${e.message.split('\n')[0]}`)
  }
}

if (shotPath) {
  await page.screenshot({ path: shotPath, fullPage: true })
}

await page.close()

const payload = { url, rects, pairs, warnings, shot: shotPath || null }
if (opts.explain.length) payload.explained = explained
if (opts.rules.length) payload.rules = ruleReports
if (opts.overlaps.length) payload.overlaps = overlapReports
if (overflow) payload.overflow = overflow
if (tokens) payload.tokens = tokens
return payload
}

// ---- baseline: measure the same route against another state of the tree -----
// Swaps `frontend/src` to `<ref>`, re-measures (vite HMR re-renders the open
// page), then puts the tree back. Working-tree changes are snapshotted with
// `git stash create` — a dangling commit that is NOT pushed onto the stash
// list, so a crash leaves the sha recoverable rather than silently consuming
// a stash entry. Untracked files are not swapped (checkout doesn't touch
// them), so a brand-new component renders in both passes.
const SWAP_PATHS = ['frontend/src']

function git(args, { check = true } = {}) {
  const r = spawnSync('git', args, { cwd: ROOT, encoding: 'utf8' })
  if (check && r.status !== 0) {
    throw new Error(`git ${args.join(' ')}: ${(r.stderr || r.stdout || '').trim()}`)
  }
  return (r.stdout || '').trim()
}

function shotFor(path, suffix) {
  if (!path) return null
  const dot = path.lastIndexOf('.')
  return dot <= 0 ? `${path}${suffix}` : `${path.slice(0, dot)}${suffix}${path.slice(dot)}`
}

/** Numeric deltas between the two passes, so the diff reads without eyeballing. */
function diffPasses(cur, base) {
  const out = {}
  if (cur.overflow && base.overflow) {
    out.overflow = {
      baseline: base.overflow.verdict,
      current: cur.overflow.verdict,
      scrollWidth: `${base.overflow.scrollWidth} → ${cur.overflow.scrollWidth}`,
      delta: cur.overflow.overflowsBy - base.overflow.overflowsBy,
      fixed: base.overflow.overflowsBy > 1 && cur.overflow.overflowsBy <= 1,
      regressed: base.overflow.overflowsBy <= 1 && cur.overflow.overflowsBy > 1,
    }
  }
  const rects = []
  for (const c of cur.rects) {
    const b = base.rects.find((r) => r.selector === c.selector)
    if (!b) continue
    if (!c.found || !b.found) { rects.push({ selector: c.selector, baselineFound: b.found, currentFound: c.found }); continue }
    const d = { selector: c.selector }
    for (const k of ['width', 'height', 'left', 'right']) {
      if (Math.abs(c[k] - b[k]) > 0.5) d[k] = `${b[k]} → ${c[k]} (${c[k] - b[k] > 0 ? '+' : ''}${+(c[k] - b[k]).toFixed(1)})`
    }
    if (Object.keys(d).length > 1) rects.push(d)
  }
  if (rects.length) out.rects = rects
  return out
}

let payload
if (!opts.baseline) {
  payload = await measurePass(opts.shot)
} else {
  const ref = git(['rev-parse', '--short', opts.baseline])
  const snapshot = git(['stash', 'create']) || null
  let swapped = false
  const restore = () => {
    if (!swapped) return
    swapped = false
    try {
      git(['checkout', 'HEAD', '--', ...SWAP_PATHS])
      if (snapshot) git(['checkout', snapshot, '--', ...SWAP_PATHS])
      // `git checkout <ref> -- <path>` writes the INDEX as well as the
      // worktree, so the three lines above would leave the restored files
      // staged — silently rewriting a staging area the user may have
      // curated. `stash create` records the pre-run index as the snapshot's
      // second parent, so reset the index back to exactly that (HEAD when
      // the tree was clean). `reset <tree-ish> -- <path>` moves the index
      // only and leaves the worktree we just restored alone.
      git(['reset', '-q', snapshot ? `${snapshot}^2` : 'HEAD', '--', ...SWAP_PATHS])
    } catch (e) {
      console.error(`\n!! FAILED TO RESTORE THE WORKING TREE: ${e.message}`)
      if (snapshot) console.error(`!! recover your changes with: git checkout ${snapshot} -- ${SWAP_PATHS.join(' ')}`)
    }
  }
  process.on('SIGINT', () => { restore(); process.exit(130) })

  try {
    const current = await measurePass(shotFor(opts.shot, '.current'))
    git(['checkout', opts.baseline, '--', ...SWAP_PATHS])
    swapped = true
    await new Promise((r) => setTimeout(r, opts.baselineWait)) // let vite HMR rebuild
    const baseline = await measurePass(shotFor(opts.shot, '.baseline'))
    restore()
    payload = {
      url,
      baselineRef: `${opts.baseline} (${ref})`,
      snapshot,
      unchanged: !snapshot && opts.baseline === 'HEAD',
      current,
      baseline,
      diff: diffPasses(current, baseline),
    }
    if (payload.unchanged) {
      payload.note = 'working tree is clean at HEAD — both passes measured the same code'
    }
  } finally {
    restore()
  }
}

await browser.close()
console.log(JSON.stringify(payload, null, 2))
