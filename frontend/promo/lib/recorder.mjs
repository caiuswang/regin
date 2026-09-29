import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'
import { chromium } from 'playwright'

import { APP_BASE, DSF, FRAME, VIEWPORT } from './env.mjs'
import { cardHtml, installOverlay } from './overlay.mjs'

// Silence between two lines. The subtitle check finds each line's speech
// onset as the end of a silence on the mixed track, and two clips placed
// back to back have no silence between them to end.
export const LINE_GAP_S = 0.45
const GLIDE_STEPS = 28
const CARD_PATH = '/__promo/card'
// Mirrors the replica's own allowlist (web/app.py): login, and the realtime
// handshake, whose ticket and topic set live in server memory only.
const POST_ALLOWED = new Set(['/api/auth/login', '/api/auth/stream-ticket', '/api/notifications/stream/topics'])

// What a hero frame must not show (R9). Selectors, plus visible text for the
// states that have no hook of their own.
const ERROR_SELECTORS = [
  '[data-testid="trace-load-error"]', '[data-testid="overview-error"]', '[role="alert"]',
]
const ERROR_TEXT = [/Page not found/, /"error":\s*"replica"/, /could not be read/i]

export function fillRoute(route, pins) {
  return route?.replace(/\{(feature|task|defect|session0)\}/g, (_, k) =>
    String(k === 'session0' ? pins.sessions[0] : pins[k]))
}

export async function record({ storyboard, sceneFns, durations, outDir, auth }) {
  const heroDir = join(outDir, 'hero'), textDir = join(outDir, 'text'), frameDir = join(outDir, 'frames')
  rmSync(frameDir, { recursive: true, force: true })
  for (const d of [heroDir, textDir, frameDir]) mkdirSync(d, { recursive: true })

  // Without the process-level scale, headless screencast frames come back at
  // CSS-pixel size (1536x864) even under a 1.25 deviceScaleFactor.
  const browser = await chromium.launch({ args: [`--force-device-scale-factor=${DSF}`] })
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: DSF, colorScheme: 'dark' })
  const consoleErrors = [], blockedWrites = []
  await context.addInitScript(({ token, user }) => {
    localStorage.setItem('regin_auth_token', token)
    localStorage.setItem('regin_auth_user', JSON.stringify(user))
    // The app keeps its own theme and does not follow prefers-color-scheme.
    localStorage.setItem('regin_theme', 'dark')
  }, auth)
  await context.addInitScript(installOverlay)
  // Defence in depth behind the replica's own 403: a write never leaves the
  // browser, and each one attempted is on the record.
  await context.route((url) => url.pathname.startsWith('/api/'), (route) => {
    const req = route.request(), path = new URL(req.url()).pathname
    const readOnly = ['GET', 'HEAD', 'OPTIONS'].includes(req.method())
    if (readOnly || (req.method() === 'POST' && POST_ALLOWED.has(path))) return route.continue()
    blockedWrites.push(`${req.method()} ${path}`)
    return route.abort('blockedbyclient')
  })

  const page = await context.newPage()
  const t0 = performance.now(), t0Wall = Date.now() / 1000
  const now = () => (performance.now() - t0) / 1000
  const screencast = await startScreencast(context, page, frameDir, t0Wall)
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`))
  let card = null
  await page.route(`${APP_BASE}${CARD_PATH}`, (route) =>
    route.fulfill({ contentType: 'text/html; charset=utf-8', body: cardHtml(card) }))

  const manifestScenes = []
  try {
    for (const scene of storyboard.scenes) {
      const entry = await playScene({ page, scene, storyboard, sceneFns, durations, now, heroDir, textDir,
        showCard: async (c) => { card = c; await page.goto(`${APP_BASE}${CARD_PATH}`) } })
      manifestScenes.push(entry)
    }
    await page.evaluate(() => window.__promo?.setSub(''))
  } finally {
    await screencast.stop()
    await context.close()
    await browser.close()
  }
  writeFrameList(screencast.frames, frameDir, manifestScenes.at(-1)?.end_s ?? now())
  return { scenes: manifestScenes, console_errors: consoleErrors, blocked_writes: blockedWrites,
    capture: { method: 'cdp-screencast', frames: screencast.frames.length } }
}

// D7's fallback, taken: `recordVideo` encodes at CSS-pixel size (1536x864
// letterboxed into the 1920x1080 canvas) whatever the deviceScaleFactor, which
// fails R8 before bitrate even matters. The screencast delivers one frame per
// repaint, each stamped with its own wall-clock time.
async function startScreencast(context, page, frameDir, t0Wall) {
  const cdp = await context.newCDPSession(page)
  const frames = []
  cdp.on('Page.screencastFrame', ({ data, metadata, sessionId }) => {
    const file = `${String(frames.length).padStart(6, '0')}.jpg`
    writeFileSync(join(frameDir, file), Buffer.from(data, 'base64'))
    frames.push({ file, t: metadata.timestamp - t0Wall })
    cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {})
  })
  await cdp.send('Page.startScreencast', {
    format: 'jpeg', quality: 95, maxWidth: FRAME.width, maxHeight: FRAME.height, everyNthFrame: 1,
  })
  return { frames, stop: () => cdp.send('Page.stopScreencast').catch(() => {}) }
}

// An ffconcat list holding each frame until the next one arrived: the
// screencast only sends a frame when the page repaints.
function writeFrameList(frames, frameDir, end) {
  if (!frames.length) throw new Error('the screencast delivered no frames')
  const out = ['ffconcat version 1.0']
  frames.forEach((f, i) => {
    const from = i === 0 ? 0 : Math.max(f.t, 0)
    const to = i + 1 < frames.length ? Math.max(frames[i + 1].t, 0) : Math.max(end, from + 0.04)
    out.push(`file '${f.file}'`, `duration ${Math.max(to - from, 0.001).toFixed(6)}`)
  })
  out.push(`file '${frames.at(-1).file}'`)
  writeFileSync(join(frameDir, 'frames.ffconcat'), `${out.join('\n')}\n`)
}

async function playScene({ page, scene, storyboard, sceneFns, durations, now, heroDir, textDir, showCard }) {
  const pins = storyboard.pins
  const entry = {
    id: scene.id, route: scene.route, surface: scene.surface, start_s: now(), end_s: null,
    hero_s: null, visited: [], hero_errors: [], lines: [],
  }
  const onNav = (frame) => {
    if (frame !== page.mainFrame()) return
    const path = new URL(frame.url()).pathname
    if (path !== CARD_PATH && entry.visited.at(-1) !== path) entry.visited.push(path)
  }
  page.on('framenavigated', onNav)
  let next = 0

  const ctx = {
    pins,
    route: fillRoute(scene.route, pins),
    url: (path) => `${APP_BASE}${path}`,
    async line(i, during) {
      if (i !== next) throw new Error(`scene ${scene.id}: line ${i} played out of order (expected ${next})`)
      const line = scene.lines[i]
      if (!line) throw new Error(`scene ${scene.id}: no line ${i}`)
      next += 1
      const dur = durations[line.id]
      await page.evaluate(installOverlay)
      await page.evaluate((t) => window.__promo.setSub(t), line.text)
      const start = now()
      entry.lines.push({ id: line.id, start_s: start, dur_s: dur })
      await Promise.all([during ? during() : null, sleep(dur * 1000)])
      await page.evaluate(() => window.__promo.setSub(''))
      await sleep(LINE_GAP_S * 1000)
    },
    async hero() {
      if (!scene.hero) throw new Error(`scene ${scene.id} is a card or has no hero selector`)
      if (entry.hero_s !== null) throw new Error(`scene ${scene.id}: hero() called twice`)
      await page.locator(scene.hero).first().waitFor({ state: 'visible' })
      await page.evaluate(() => document.fonts.ready)
      entry.hero_errors = await visibleErrors(page)
      const before = now()
      await page.screenshot({ path: join(heroDir, `${scene.id}.png`) })
      entry.hero_s = (before + now()) / 2
      writeFileSync(join(textDir, `${scene.id}.txt`), await page.evaluate(() => document.body.innerText))
    },
    async point(selector) {
      const target = page.locator(selector).first()
      await target.scrollIntoViewIfNeeded()
      const box = await target.boundingBox()
      if (!box) throw new Error(`point(${selector}): target has no box`)
      const x = box.x + box.width / 2, y = box.y + box.height / 2
      await page.mouse.move(x, y)
      await page.evaluate(([x, y, n]) => window.__promo.glide(x, y, n), [x, y, GLIDE_STEPS])
      const c = await page.evaluate(() => window.__promo.cursorCentre())
      const at = await target.boundingBox()
      const inside = at && c.x >= at.x && c.x <= at.x + at.width && c.y >= at.y && c.y <= at.y + at.height
      if (!inside) {
        throw new Error(`point(${selector}): cursor at ${c.x.toFixed(0)},${c.y.toFixed(0)} is outside the target — click refused`)
      }
      await page.mouse.click(c.x, c.y)
    },
  }

  try {
    if (scene.surface === 'card') {
      await showCard({ title: scene.title ?? storyboard.title, caption: scene.caption })
      for (let i = 0; i < scene.lines.length; i += 1) await ctx.line(i)
    } else {
      const fn = sceneFns[scene.id]
      if (!fn) throw new Error(`scene ${scene.id} has no function in scenes.mjs`)
      await fn(page, ctx)
      if (entry.hero_s === null) throw new Error(`scene ${scene.id} never called ctx.hero()`)
    }
    if (next !== scene.lines.length) {
      throw new Error(`scene ${scene.id} played ${next} of ${scene.lines.length} lines`)
    }
  } finally {
    page.off('framenavigated', onNav)
  }
  entry.end_s = now()
  return entry
}

async function visibleErrors(page) {
  return page.evaluate(([sels, texts]) => {
    const seen = []
    const visible = (el) => {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el)
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
    }
    for (const sel of sels) {
      for (const el of document.querySelectorAll(sel)) if (visible(el)) seen.push(`${sel}: ${el.innerText.slice(0, 120)}`)
    }
    const body = document.body.innerText
    for (const src of texts) {
      const m = body.match(new RegExp(src[0], src[1]))
      if (m) seen.push(`text: ${m[0]}`)
    }
    return seen
  }, [ERROR_SELECTORS, ERROR_TEXT.map((r) => [r.source, r.flags])])
}
