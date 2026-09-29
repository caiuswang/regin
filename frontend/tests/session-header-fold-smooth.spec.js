/**
 * The trace-header fold must GLIDE where a glide is safe, and stay instant
 * where it is not. A layout tween that overlaps an in-flight native smooth
 * scroll kills the scroll (Chromium cancels the scroll animation on a layout
 * change — verified: a mid-glide wheel flick died and stranded the reader
 * mid-feed), so the scroll-driven collapse — which fires mid-gesture by
 * definition — swaps instantly, softened by the compact row's compositor
 * fade. The pull-driven expand commits only once the scroller has settled at
 * the top, and manual toggles fire on a click/keypress: both glide through
 * intermediate heights. Proven with per-frame geometry sampling, not eyeballing —
 * screenshots can't catch a one-frame jump.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'
import { wheelOverTrace } from './helpers/traceScroller.js'

async function seedScrollableSession(page) {
  const traceId = randomUUID()
  const spans = []
  const filler = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. '.repeat(30)
  for (let i = 0; i < 20; i++) {
    const pid = `p-${traceId.slice(0, 8)}-${i}`
    spans.push({
      trace_id: traceId,
      span_id: pid,
      parent_id: null,
      name: 'prompt',
      start_time: `2026-05-09T10:${String(i).padStart(2, '0')}:00`,
      attributes: { text: `prompt number ${i} — padding the feed so the page scrolls`, is_test: true },
    })
    spans.push({
      trace_id: traceId,
      span_id: `a-${traceId.slice(0, 8)}-${i}`,
      parent_id: pid,
      name: 'assistant_response',
      start_time: `2026-05-09T10:${String(i).padStart(2, '0')}:05`,
      attributes: { text: filler, is_test: true },
    })
  }
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  return traceId
}

const wheel = wheelOverTrace

// The reveal gesture — see session-header-collapse.spec.js. 420px of credited
// pull at 26px an event, with headroom for the events the dwell gate eats.
const pull = (page) => wheel(page, -60, 24)

// Sample the sticky wrapper's height every animation frame while `action`
// runs, then reduce to the count of DISTINCT intermediate heights — values
// clear of both settled endpoints. An instant swap yields 0; a tween yields
// a staircase. `tweenFrames` counts frames with a running height animation
// anywhere in the header — the glide itself, as opposed to a one-off reflow
// (the compact header re-wrapping once its late content lands).
async function sampleFold(page, action, ms = 900) {
  const sampler = page.evaluate((windowMs) => new Promise((resolve) => {
    window.__foldSampling = true
    const el = document.querySelector('[data-testid="trace-sticky-header"]')
    const heights = []
    let tweenFrames = 0
    const tweensHeight = (a) => a.playState === 'running'
      && (a.effect?.getKeyframes?.() || []).some((k) => 'height' in k)
    const t0 = performance.now()
    function tick() {
      heights.push(el.getBoundingClientRect().height)
      if (el.getAnimations({ subtree: true }).some(tweensHeight)) tweenFrames++
      if (performance.now() - t0 < windowMs) requestAnimationFrame(tick)
      else resolve({ heights, tweenFrames })
    }
    // The pre-action height, read now: the first frame can land after an
    // instant swap has already happened, losing the endpoint it folds from.
    heights.push(el.getBoundingClientRect().height)
    requestAnimationFrame(tick)
  }), ms)
  await page.waitForFunction(() => window.__foldSampling === true)
  await action()
  const { heights, tweenFrames } = await sampler
  await page.evaluate(() => { delete window.__foldSampling })
  const lo = Math.min(...heights)
  const hi = Math.max(...heights)
  const intermediates = new Set(
    heights.filter((h) => h > lo + 3 && h < hi - 3).map((h) => Math.round(h)),
  )
  return { heights, lo, hi, intermediates: intermediates.size, tweenFrames }
}

test('expand at the top and manual toggles glide; the scroll-collapse swaps instantly', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  const digest = page.getByTestId('trace-header-digest')
  await expect(vitals).toBeVisible({ timeout: 10_000 })

  // Scroll-driven collapse is INSTANT by design: it fires mid-gesture, and
  // a layout tween there would kill the reader's own in-flight scroll.
  const collapse = await sampleFold(page, () => wheel(page, 1500))
  await expect(digest).toBeVisible()
  expect(collapse.hi - collapse.lo, 'the fold must actually happen').toBeGreaterThan(40)
  expect(collapse.tweenFrames, 'scroll-collapse must not glide').toBe(0)

  // Expanding is a deliberate pull now, and it can only commit once the
  // scroller has settled at the top — so it always fires on a settled scroll
  // and is always safe to glide. Sampled across the whole gesture: the commit
  // lands partway through the wheel run, not after the call returns.
  await wheel(page, -2500, 3)
  // Clears the 1.2s post-hide cooldown and the 250ms dwell, so the pull below
  // is measuring the glide rather than the gates.
  await page.waitForTimeout(1400)
  const expand = await sampleFold(page, () => pull(page), 2000)
  await expect(vitals).toBeVisible()
  expect(expand.intermediates,
    `expand jumped ${expand.lo}→${expand.hi}px without a glide`).toBeGreaterThanOrEqual(3)

  // Manual toggles glide in BOTH directions — a keypress means the scroll
  // is settled.
  const foldDown = await sampleFold(page, () => page.keyboard.press('h'))
  await expect(digest).toBeVisible()
  expect(foldDown.intermediates, 'manual collapse should glide').toBeGreaterThanOrEqual(3)
  // The detector the no-glide assertions above rely on does see a real glide.
  expect(foldDown.tweenFrames, 'a glide the tween detector missed').toBeGreaterThan(0)

  const foldUp = await sampleFold(page, () => page.keyboard.press('h'))
  await expect(vitals).toBeVisible()
  expect(foldUp.intermediates, 'manual expand should glide').toBeGreaterThanOrEqual(3)
})

test('a rapid re-toggle mid-glide settles at the natural height with no leftover clip', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  await expect(vitals).toBeVisible({ timeout: 10_000 })
  const settledFull = await page.evaluate(() =>
    document.querySelector('[data-testid="trace-sticky-header"]').getBoundingClientRect().height)

  // Reverse the fold while its tween is still running.
  await page.keyboard.press('h')
  await page.waitForTimeout(80)
  await page.keyboard.press('h')
  await page.waitForTimeout(800)

  await expect(vitals).toBeVisible()
  const settled = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="trace-sticky-header"]')
    return {
      height: el.getBoundingClientRect().height,
      inlineOverflow: el.style.overflow,
      running: el.getAnimations().length,
    }
  })
  expect(settled.inlineOverflow, 'clip stuck on after the glide').toBe('')
  expect(settled.running).toBe(0)
  expect(Math.abs(settled.height - settledFull),
    'header stranded at a mid-glide height').toBeLessThan(2)
})

test('prefers-reduced-motion folds in a single frame', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  await expect(page.getByTestId('trace-vitals-strip')).toBeVisible({ timeout: 10_000 })
  const fold = await sampleFold(page, () => page.keyboard.press('h'), 600)
  await expect(page.getByTestId('trace-header-digest')).toBeVisible()
  // Relative, not a pixel count: the compact header's height is the design's
  // to change, the fold's existence is what is pinned here.
  expect(fold.lo, 'the fold itself must still happen').toBeLessThan(fold.hi * 0.8)
  expect(fold.tweenFrames, 'reduced-motion must not tween').toBe(0)
})
