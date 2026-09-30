import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { setTimeout as sleep } from 'node:timers/promises'
import { chromium } from 'playwright'

import { APP_BASE, DSF, FRAME, VIEWPORT } from './env.mjs'
import { installOverlay } from './overlay.mjs'

// Silence between two lines. The subtitle check finds each line's speech
// onset as the end of a silence on the mixed track, and two clips placed
// back to back have no silence between them to end.
export const LINE_GAP_S = 0.45
const DISSOLVE_MS = 560
const CARD_MS = 900
const CAMERA_MS = 1100
// A page is settled once this many consecutive frames pass with no change to
// its elements or attributes, no scroll and no finite animation running, and
// no /api request is in flight. Counted in frames, not milliseconds (I3).
// Text-only changes do not count: a live clock ("waiting 7m 3s") ticks for
// as long as the page is open, and under 4K capture a second can be fewer
// frames than the quiet run needs.
const QUIET_FRAMES = 12
const SETTLE_MAX_FRAMES = 1200
// Keeps a target clear of the subtitle band, and a 'start' target clear of
// the sticky section headers, when the camera brings it into view.
const SAFE = { top: 24, bottom: 110, sticky: 64 }
const PLATE_PATH = '/__promo/plate/'
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

const cardOf = (scene, storyboard) => ({ title: scene.title ?? storyboard.title, caption: scene.caption ?? null })
const pathOf = (route) => (route ? new URL(route, APP_BASE).pathname : null)

// Every /api request the page has in flight, streams excepted: a settled
// page is one whose data has arrived.
function trackApi(context) {
  let inflight = 0
  const waiters = []
  const counted = (req) => {
    const path = new URL(req.url()).pathname
    return path.startsWith('/api/') && !path.includes('/stream') &&
      !['eventsource', 'websocket'].includes(req.resourceType())
  }
  context.on('request', (req) => { if (counted(req)) inflight += 1 })
  const done = (req) => {
    if (!counted(req)) return
    inflight -= 1
    if (inflight === 0) waiters.splice(0).forEach((f) => f())
  }
  context.on('requestfinished', done)
  context.on('requestfailed', done)
  return {
    get inflight() { return inflight },
    idle: () => (inflight === 0 ? Promise.resolve() : new Promise((ok) => waiters.push(ok))),
  }
}

function quietFrames([need, max]) {
  return new Promise((done) => {
    let quiet = 0, total = 0, dirty = false
    const mark = () => { dirty = true }
    const text = (n) => n.nodeType === Node.TEXT_NODE
    const textOnly = (r) => r.type === 'characterData' ||
      (r.type === 'childList' && [...r.addedNodes].every(text) && [...r.removedNodes].every(text))
    const mo = new MutationObserver((records) => { if (!records.every(textOnly)) mark() })
    mo.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true })
    document.addEventListener('scroll', mark, true)
    const moving = () => document.getAnimations().some((a) => {
      if (a.playState !== 'running') return false
      if (a.effect?.target?.closest?.('#promo-layer')) return false
      return Number.isFinite(a.effect?.getComputedTiming().endTime)
    })
    const finish = (ok) => { mo.disconnect(); document.removeEventListener('scroll', mark, true); done(ok) }
    const tick = () => {
      total += 1
      if (dirty || moving()) { quiet = 0; dirty = false } else quiet += 1
      if (quiet >= need) finish(true)
      else if (total >= max) finish(false)
      else requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  })
}

// Retries only when a request started during the quiet run; a page that
// never stops changing fails at once, naming the step.
async function settle(page, net, what) {
  for (let round = 0; round < 20; round += 1) {
    await net.idle()
    await page.evaluate(() => document.fonts.ready)
    const quiet = await page.evaluate(quietFrames, [QUIET_FRAMES, SETTLE_MAX_FRAMES])
    if (!quiet) throw new Error(`${what}: the page kept changing for ${SETTLE_MAX_FRAMES} frames`)
    if (net.inflight === 0) return
  }
  throw new Error(`${what}: /api requests kept starting (${net.inflight} in flight)`)
}

async function routerPush(page, path) {
  await page.evaluate((p) => document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(p), path)
}

export async function record({ storyboard, sceneFns, durations, outDir, auth }) {
  const heroDir = join(outDir, 'hero'), textDir = join(outDir, 'text'), frameDir = join(outDir, 'frames')
  rmSync(frameDir, { recursive: true, force: true })
  for (const d of [heroDir, textDir, frameDir]) mkdirSync(d, { recursive: true })
  const pins = storyboard.pins

  // Without the process-level scale, headless screencast frames come back at
  // CSS-pixel size even under a deviceScaleFactor.
  const browser = await chromium.launch({ args: [`--force-device-scale-factor=${DSF}`] })
  const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: DSF, colorScheme: 'dark' })
  const consoleErrors = [], blockedWrites = []
  await context.addInitScript(({ token, user }) => {
    localStorage.setItem('regin_auth_token', token)
    localStorage.setItem('regin_auth_user', JSON.stringify(user))
    // The app keeps its own theme and does not follow prefers-color-scheme.
    localStorage.setItem('regin_theme', 'dark')
    // The rail stays folded to its icon column for the whole film.
    localStorage.setItem('regin_sidebar_collapsed', '1')
  }, auth)
  const first = storyboard.scenes[0]
  await context.addInitScript(installOverlay, { card: first.surface === 'card' ? cardOf(first, storyboard) : null })
  // Defence in depth behind the replica's own 403: a write never leaves the
  // browser, and each one attempted is on the record.
  await context.route((url) => url.pathname.startsWith('/api/'), (route) => {
    const req = route.request(), path = new URL(req.url()).pathname
    const readOnly = ['GET', 'HEAD', 'OPTIONS'].includes(req.method())
    if (readOnly || (req.method() === 'POST' && POST_ALLOWED.has(path))) return route.continue()
    blockedWrites.push(`${req.method()} ${path}`)
    return route.abort('blockedbyclient')
  })
  const net = trackApi(context)

  const page = await context.newPage()
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`))
  let plate = null
  await page.route(`${APP_BASE}${PLATE_PATH}**`, (route) => route.fulfill({ contentType: 'image/jpeg', body: plate }))

  const screencast = await startScreencast(context, page, frameDir)
  let t0 = null
  const clock = () => Date.now() / 1000 - t0

  const cam = { page, net, plateSeq: 0, setPlate: (buf) => { plate = buf } }

  // Off the record: the first app route loads beneath the opening card, so
  // the film never shows a document loading.
  const firstApp = storyboard.scenes.find((s) => s.surface !== 'card')
  await page.goto(`${APP_BASE}${fillRoute(firstApp.route, pins)}`)
  await page.locator(firstApp.hero).first().waitFor({ state: 'visible' })
  await settle(page, net, 'boot')
  t0 = Date.now() / 1000

  const manifestScenes = []
  try {
    for (const [i, scene] of storyboard.scenes.entries()) {
      manifestScenes.push(await playScene({
        page, scene, next: storyboard.scenes[i + 1], storyboard, sceneFns, durations, clock, cam, heroDir, textDir,
      }))
    }
  } finally {
    await screencast.stop()
    await context.close()
    await browser.close()
  }
  const end = manifestScenes.at(-1).end_s
  const frames = fromT0(screencast.frames, t0)
  for (const entry of manifestScenes) {
    if (entry.hero_s !== null) entry.hero_s = frameShownAt(frames, entry.hero_s, entry.id)
  }
  writeFrameList(frames, frameDir, end)
  return {
    scenes: manifestScenes, console_errors: consoleErrors, blocked_writes: blockedWrites,
    capture: { method: 'cdp-screencast', frames: frames.length, size: FRAME },
  }
}

// Each frame is stamped with the wall-clock time it was produced, and the
// screencast only sends one when the page repaints.
async function startScreencast(context, page, frameDir) {
  const cdp = await context.newCDPSession(page)
  const frames = []
  cdp.on('Page.screencastFrame', ({ data, metadata, sessionId }) => {
    const file = `${String(frames.length).padStart(6, '0')}.jpg`
    writeFileSync(join(frameDir, file), Buffer.from(data, 'base64'))
    frames.push({ file, ts: metadata.timestamp })
    cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {})
  })
  await cdp.send('Page.startScreencast', {
    format: 'jpeg', quality: 95, maxWidth: FRAME.width, maxHeight: FRAME.height, everyNthFrame: 1,
  })
  return { frames, stop: () => cdp.send('Page.stopScreencast').catch(() => {}) }
}

// The last frame before t0 — the opening card, loaded and settled — opens
// the film; everything before it is the boot.
function fromT0(frames, t0) {
  const open = frames.findLastIndex((f) => f.ts <= t0)
  return frames.slice(Math.max(open, 0)).map((f) => ({ file: f.file, t: Math.max(f.ts - t0, 0) }))
}

// The frame on screen when page.screenshot() returned. Its capture repaints
// only when the page changed during Playwright's pre-capture steps, and that
// frame is stamped just before the screenshot returns; a still page yields no
// frame at all, so an earlier one is the hero.
function frameShownAt(frames, shotEnd, sceneId) {
  const frame = frames.findLast((f) => f.t <= shotEnd)
  if (!frame) throw new Error(`scene ${sceneId}: no screencast frame by the end of its hero screenshot`)
  return frame.t
}

// An ffconcat list holding each frame until the next one arrived.
function writeFrameList(frames, frameDir, end) {
  if (!frames.length) throw new Error('the screencast delivered no frames')
  const out = ['ffconcat version 1.0']
  frames.forEach((f, i) => {
    const to = i + 1 < frames.length ? frames[i + 1].t : Math.max(end, f.t + 0.04)
    out.push(`file '${f.file}'`, `duration ${Math.max(to - f.t, 0.001).toFixed(6)}`)
  })
  out.push(`file '${frames.at(-1).file}'`)
  writeFileSync(join(frameDir, 'frames.ffconcat'), `${out.join('\n')}\n`)
}

// Lays a still of the current frame over everything, so whatever happens
// beneath it is off camera until `thaw` dissolves it away. The still is the
// frame as shown, so putting it up changes no pixel: nothing is ever drawn
// that the film must not see. (Dropping frames by their timestamps instead
// does not hold: a frame drawn before a change can be stamped after it.)
async function freeze(cam) {
  cam.setPlate(await cam.page.screenshot({ type: 'jpeg', quality: 95 }))
  cam.plateSeq += 1
  await cam.page.evaluate((url) => window.__promo.freeze(url), `${APP_BASE}${PLATE_PATH}${cam.plateSeq}.jpg`)
}

async function thaw(cam) {
  await cam.page.evaluate(() => { if (window.__promo.zoomed()) window.__promo.cameraReset() })
  await cam.page.evaluate((ms) => window.__promo.thaw(ms), DISSOLVE_MS)
}

async function railState(page) {
  return page.evaluate(() => {
    const rails = [...document.querySelectorAll('aside.sidebar')]
    if (!rails.length) return 'absent'
    return rails.every((r) => r.classList.contains('is-collapsed')) ? 'collapsed' : 'expanded'
  })
}

// The eased camera scroll: the nearest scrolling ancestor glides until the
// target sits at `block` ('start' | 'center') of the visible area.
function easedScroll(el, [block, safe, instant]) {
  let box = el.parentElement
  while (box && !(box.scrollHeight > box.clientHeight && /(auto|scroll)/.test(getComputedStyle(box).overflowY))) {
    box = box.parentElement
  }
  box ??= document.scrollingElement
  const r = el.getBoundingClientRect(), b = box === document.scrollingElement ? { top: 0, height: innerHeight } : box.getBoundingClientRect()
  const top = Math.max(b.top, safe.top), bottom = Math.min(b.top + b.height, innerHeight - safe.bottom)
  const want = block === 'start' ? top + safe.sticky : (top + bottom) / 2 - r.height / 2
  const from = box.scrollTop
  const to = Math.max(0, Math.min(box.scrollHeight - box.clientHeight, from + (r.top - want)))
  const d = to - from
  if (Math.abs(d) < 2) return Promise.resolve()
  if (instant) { box.scrollTop = to; return Promise.resolve() }
  const ms = Math.min(1400, Math.max(550, 320 + Math.abs(d) * 0.7))
  return new Promise((done) => {
    const t0 = performance.now()
    const tick = (now) => {
      const t = Math.min(1, (now - t0) / ms), e = t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2
      box.scrollTop = from + d * e
      if (t < 1) requestAnimationFrame(tick); else done()
    }
    requestAnimationFrame(tick)
  })
}

async function playScene({ page, scene, next, storyboard, sceneFns, durations, clock, cam, heroDir, textDir }) {
  const pins = storyboard.pins
  const entry = {
    id: scene.id, route: scene.route, surface: scene.surface, start_s: clock(), end_s: null,
    hero_s: null, visited: [], hero_errors: [], rail_at_hero: null, lines: [],
  }
  const onNav = (frame) => {
    if (frame !== page.mainFrame()) return
    const path = new URL(frame.url()).pathname
    if (entry.visited.at(-1) !== path) entry.visited.push(path)
  }
  const onRoute = scene.surface !== 'card'
  if (onRoute) {
    entry.visited.push(new URL(page.url()).pathname)
    page.on('framenavigated', onNav)
  }
  let played = 0

  const target = async (selector) => {
    const loc = page.locator(selector).first()
    await loc.waitFor({ state: 'visible' })
    return loc
  }

  const ctx = {
    pins,
    route: fillRoute(scene.route, pins),
    async line(i, during) {
      if (i !== played) throw new Error(`scene ${scene.id}: line ${i} played out of order (expected ${played})`)
      const line = scene.lines[i]
      if (!line) throw new Error(`scene ${scene.id}: no line ${i}`)
      played += 1
      const dur = durations[line.id]
      await page.evaluate((t) => window.__promo.sub(t), line.text)
      const start = clock()
      entry.lines.push({
        id: line.id, start_s: start, dur_s: dur,
        rail: await railState(page),
        subtitle_layers: await page.evaluate(() => window.__promo.subLayers()),
        band: await page.evaluate(() => window.__promo.subBox()),
      })
      await Promise.all([during ? during() : null, sleep(dur * 1000)])
      // The band holds this line until now, even when `during` outran the audio.
      entry.lines.at(-1).shown_until = clock()
      if (played === scene.lines.length) await page.evaluate(() => window.__promo.sub(''))
      await sleep(LINE_GAP_S * 1000)
    },
    async hero() {
      if (!scene.hero) throw new Error(`scene ${scene.id} is a card or has no hero selector`)
      if (entry.hero_s !== null) throw new Error(`scene ${scene.id}: hero() called twice`)
      await target(scene.hero)
      await settle(page, cam.net, `scene ${scene.id} hero`)
      entry.hero_errors = await visibleErrors(page)
      entry.rail_at_hero = await railState(page)
      await page.screenshot({ path: join(heroDir, `${scene.id}.png`) })
      // On the recording clock; record() moves it onto the frame shown at this instant.
      entry.hero_s = clock()
      writeFileSync(join(textDir, `${scene.id}.txt`), await page.evaluate(() => document.body.innerText))
    },
    async scrollTo(selector, block = 'center', { instant = false } = {}) {
      const loc = page.locator(selector).first()
      await loc.waitFor({ state: 'attached' })
      await loc.evaluate(easedScroll, [block, SAFE, instant])
    },
    // A move too long to glide: the page jumps beneath a freeze plate and
    // dissolves in at its new position.
    async jumpTo(selector, block = 'start') {
      const loc = page.locator(selector).first()
      await loc.waitFor({ state: 'attached' })
      await freeze(cam)
      await loc.evaluate(easedScroll, [block, SAFE, true])
      await settle(page, cam.net, `scene ${scene.id} jumpTo(${selector})`)
      await thaw(cam)
    },
    // Brings the target into view if it is not, then glides the pointer to
    // it. Returns the point the pointer rests on.
    async aim(selector) {
      const loc = await target(selector)
      const inView = await loc.evaluate((el, safe) => {
        const r = el.getBoundingClientRect()
        return r.top >= safe.top && r.bottom <= innerHeight - safe.bottom && r.left >= 0 && r.right <= innerWidth
      }, SAFE)
      if (!inView) await ctx.scrollTo(selector)
      const box = await loc.boundingBox()
      if (!box) throw new Error(`aim(${selector}): target has no box`)
      const x = box.x + box.width / 2, y = box.y + box.height / 2
      await page.evaluate(([x, y]) => window.__promo.glide(x, y), [x, y])
      await page.mouse.move(x, y)
      return loc
    },
    // R11: the click lands where the pointer visibly rests, or not at all.
    async press(loc, selector) {
      const c = await page.evaluate(() => window.__promo.hotspot())
      const at = await loc.boundingBox()
      const inside = at && c.x >= at.x && c.x <= at.x + at.width && c.y >= at.y && c.y <= at.y + at.height
      if (!inside) {
        throw new Error(`click(${selector}): pointer at ${c.x.toFixed(0)},${c.y.toFixed(0)} is outside the target — click refused`)
      }
      return c
    },
    async click(c) {
      await Promise.all([page.evaluate(() => window.__promo.ripple()), page.mouse.click(c.x, c.y)])
    },
    // A click whose effect is small and local: a menu, a toggle.
    async point(selector) {
      const loc = await ctx.aim(selector)
      await ctx.click(await ctx.press(loc, selector))
    },
    // A click that changes the whole view — a new page, a dialog, a view
    // switch — happens beneath a freeze plate and dissolves in once the new
    // view has settled, instead of cutting. `beneath` runs under the plate
    // once the new view has settled — after the app's own reactions to the
    // click — for a fix-up the camera should not show.
    async follow(selector, ready, beneath) {
      const loc = await ctx.aim(selector)
      const c = await ctx.press(loc, selector)
      await page.evaluate(() => window.__promo.ripple())
      await freeze(cam)
      await page.mouse.click(c.x, c.y)
      await target(ready)
      await settle(page, cam.net, `scene ${scene.id} follow(${selector})`)
      if (beneath) {
        await beneath()
        await settle(page, cam.net, `scene ${scene.id} follow(${selector}) beneath`)
      }
      await thaw(cam)
    },
    // Zooms in on `selector` at scale k; `null` returns to the full frame.
    async zoom(selector, k = 1.5, { left = false } = {}) {
      if (!selector) return page.evaluate((ms) => window.__promo.camera(null, 1, ms), CAMERA_MS)
      const loc = await target(selector)
      await loc.evaluate((el, [k, ms, left]) => window.__promo.camera(el, k, ms, left), [k, CAMERA_MS, left])
    },
    settle: () => settle(page, cam.net, `scene ${scene.id}`),
  }

  try {
    if (scene.surface === 'card') {
      if (!(await page.evaluate(() => window.__promo.cardUp()))) {
        await page.evaluate(([c, ms]) => window.__promo.card(c, ms), [cardOf(scene, storyboard), CARD_MS])
      }
      for (let i = 0; i < scene.lines.length; i += 1) await ctx.line(i)
      if (next && next.surface !== 'card') await leaveCard(page, cam, next, pins)
    } else {
      const fn = sceneFns[scene.id]
      if (!fn) throw new Error(`scene ${scene.id} has no function in scenes.mjs`)
      await fn(ctx)
      if (entry.hero_s === null) throw new Error(`scene ${scene.id} never called ctx.hero()`)
    }
    if (played !== scene.lines.length) {
      throw new Error(`scene ${scene.id} played ${played} of ${scene.lines.length} lines`)
    }
  } finally {
    if (onRoute) page.off('framenavigated', onNav)
  }
  entry.end_s = clock()
  return entry
}

// Beneath the card, the next scene's route is brought up and settled; then
// the card dissolves away to reveal it.
async function leaveCard(page, cam, next, pins) {
  const path = pathOf(fillRoute(next.route, pins))
  if (path && new URL(page.url()).pathname !== path) {
    await routerPush(page, fillRoute(next.route, pins))
    await page.locator(next.hero).first().waitFor({ state: 'visible' })
    await settle(page, cam.net, `card → ${next.id}`)
  }
  await page.evaluate((ms) => window.__promo.card(null, ms), CARD_MS)
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
