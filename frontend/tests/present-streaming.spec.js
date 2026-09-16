/**
 * Presentation reader, §11 streaming: a span that is still being written must
 * re-render as it grows WITHOUT throwing away the reader's scroll position.
 *
 * The append is delivered the way the live tail delivers it — a `/map` poll
 * returning a longer body for the same span_id (span ingest upserts on
 * `ON CONFLICT(trace_id, span_id)`, so a growing span is the same row getting
 * longer). Patched at the route rather than re-POSTed so the growth is
 * deterministic instead of racing the poll.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const BASE = Array.from({ length: 400 }, (_, i) => `output line ${i}`).join('\n')
const APPENDED = `${BASE}\n${Array.from({ length: 60 }, (_, i) => `APPENDED line ${i}`).join('\n')}`

async function seedLive(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const bashId = `b-${p8}`
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-08T12:00:00', attributes: { text: 'streaming', is_test: true } },
    { trace_id: traceId, span_id: bashId, parent_id: prompt, name: 'tool.Bash',
      start_time: '2026-05-08T12:00:01', duration_ms: 100,
      attributes: { tool_name: 'Bash', command: 'tail -f build.log', stdout: BASE, is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  // The span keeps growing, and the session never ends — the live shape.
  const state = { appended: false }
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.ended_at = null
    if (state.appended) {
      for (const s of body.spans || []) {
        if (s.span_id === bashId && s.attributes) s.attributes.stdout = APPENDED
      }
    }
    await route.fulfill({ response: res, json: body })
  })
  // The reader also reads the per-span /content endpoint, which is where a
  // cached first fetch can mask everything the poll delivers afterwards.
  await page.route(`**/api/sessions/${traceId}/spans/${bashId}/content*`, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    if (state.appended && body.attributes) body.attributes.stdout = APPENDED
    await route.fulfill({ response: res, json: body })
  })

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return { traceId, bashId, state }
}

// The seeded session never ends, so its poll is still in flight at teardown
// and an installed route handler outlives the page. Drop the handlers first.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {})
})

const reader = (page) => page.getByTestId('span-present-reader')
const bodyScrollTop = (page) => page.evaluate(() =>
  document.querySelector('[data-present-body]').scrollTop)

test('an append re-renders in place and keeps the reader where it was', async ({ page }) => {
  test.setTimeout(120_000)
  const { state } = await seedLive(page)

  await page.locator('.event-spine-row').filter({ hasText: 'Shell' }).first()
    .locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await expect(reader(page)).toContainText('output line 200')

  // Park the reader halfway down the body.
  await page.evaluate(() => {
    const b = document.querySelector('[data-present-body]')
    b.scrollTop = Math.floor(b.scrollHeight / 2)
  })
  const before = await bodyScrollTop(page)
  expect(before).toBeGreaterThan(100)

  // The span grows. No sleep: wait for the appended text to actually land.
  state.appended = true
  await expect(reader(page).locator('.present-term-out'))
    .toContainText('APPENDED line 59', { timeout: 30_000 })

  // …and the reader did not jump.
  const after = await bodyScrollTop(page)
  expect(Math.abs(after - before)).toBeLessThanOrEqual(2)
  // Still the same span, still open, counter unmoved.
  await expect(reader(page)).toContainText('tool.Bash')
})

test('navigating to another span still resets scroll, and an append never re-keys', async ({ page }) => {
  test.setTimeout(120_000)
  const { state } = await seedLive(page)

  await page.locator('.event-spine-row').filter({ hasText: 'Shell' }).first()
    .locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await page.evaluate(() => {
    const b = document.querySelector('[data-present-body]')
    b.scrollTop = Math.floor(b.scrollHeight / 2)
  })
  expect(await bodyScrollTop(page)).toBeGreaterThan(100)

  // Stepping to a DIFFERENT span lands at the top: arriving mid-document in
  // something you have not read is the disorientation the reset exists for.
  await page.keyboard.press('ArrowUp')
  await expect(reader(page)).toContainText('prompt')
  expect(await bodyScrollTop(page)).toBe(0)

  // Back to the streaming span; an append must not move the footer position
  // or swap the span out from under the reader.
  await page.keyboard.press('ArrowDown')
  await expect(reader(page)).toContainText('tool.Bash')
  const posBefore = await reader(page).locator('.present-count').textContent()
  state.appended = true
  await expect(reader(page).locator('.present-term-out'))
    .toContainText('APPENDED line 59', { timeout: 30_000 })
  expect(await reader(page).locator('.present-count').textContent()).toBe(posBefore)
  await expect(reader(page)).toContainText('tool.Bash')
})

test('header and footer stay pinned while a long body scrolls under them', async ({ page }) => {
  test.setTimeout(120_000)
  await seedLive(page)
  await page.locator('.event-spine-row').filter({ hasText: 'Shell' }).first()
    .locator('.present-open').click()
  await expect(reader(page)).toBeVisible()

  const box = (sel) => reader(page).locator(sel).boundingBox()
  const h0 = await box('.present-header')
  const f0 = await box('.present-footer')
  await page.evaluate(() => {
    const b = document.querySelector('[data-present-body]')
    b.scrollTop = b.scrollHeight
  })
  const h1 = await box('.present-header')
  const f1 = await box('.present-footer')
  expect(Math.abs(h1.y - h0.y)).toBeLessThanOrEqual(1)
  expect(Math.abs(f1.y - f0.y)).toBeLessThanOrEqual(1)
  // The reader still says which span this is and where it sits, at full depth.
  await expect(reader(page).locator('.present-title')).toBeVisible()
  await expect(reader(page).locator('.present-count')).toBeVisible()
})

/**
 * Scroll cost must not scale with body size.
 *
 * This is the invariant, not a millisecond budget: absolute timings differ per
 * machine, but the SHAPE is portable. Rendering every line up front costs more
 * to open and then scrolls flat (~300ms at any size); deferring the render
 * (content-visibility:auto, tried and reverted) buys 30–240ms on open and then
 * pays it back on every scroll, with the cost growing with the body — measured
 * 4.6× at 20k lines against 1.1× without. A reading surface wants the flat one.
 *
 * The store's real ceiling is 2,360 lines across 504k spans; 20k is ~8× beyond
 * anything ever recorded, so this deliberately tests well past the need.
 */
async function seedBigDiffs(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const mk = n => Array.from({ length: n }, (_, i) =>
    (i % 3 === 0 ? '+' : i % 3 === 1 ? '-' : ' ') + `const value_${i} = compute(${i})`).join('\n')
  const spans = [{ trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
    start_time: '2026-05-08T12:00:00', attributes: { text: 'perf', is_test: true } }]
  ;[2400, 20000].forEach((n, k) => spans.push({
    trace_id: traceId, span_id: `d${k}-${p8}`, parent_id: prompt, name: 'tool.Write',
    start_time: `2026-05-08T12:00:0${k + 1}`, duration_ms: 100,
    attributes: { tool_name: 'Write', file_path: `/repo/d${n}.js`, diff: mk(n), is_test: true },
  }))
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
}

async function readScrollMs(page, needle) {
  await page.locator('.event-spine-row').filter({ hasText: needle }).first()
    .locator('.present-open').click()
  await expect(reader(page)).toBeVisible({ timeout: 60_000 })
  await page.waitForFunction(() => document.querySelector('[data-present-body] .diff-block'),
    null, { timeout: 60_000 })
  const t0 = Date.now()
  await page.evaluate(async () => {
    const b = document.querySelector('[data-present-body]')
    for (let i = 0; i < 30; i++) { b.scrollTop = i * 400; await new Promise(r => requestAnimationFrame(r)) }
  })
  const ms = Date.now() - t0
  await page.keyboard.press('Escape')
  await expect(reader(page)).toHaveCount(0)
  return ms
}

test('scrolling a huge body costs no more than scrolling an ordinary one', async ({ page }) => {
  test.setTimeout(180_000)
  await seedBigDiffs(page)

  const small = await readScrollMs(page, 'd2400.js')
  const huge = await readScrollMs(page, 'd20000.js')
  // 8.3× the lines must not mean materially more scroll work. Measured ~1.1×
  // today; the reverted deferred-rendering approach measured ~4.6×.
  expect(huge / Math.max(small, 1),
    `scroll cost scaled with body size: ${small}ms at 2.4k lines vs ${huge}ms at 20k`)
    .toBeLessThan(2.5)
  // Every line stays in the DOM, so Copy and find-in-page see the whole body.
  await page.locator('.event-spine-row').filter({ hasText: 'd20000.js' }).first()
    .locator('.present-open').click()
  const chars = await page.evaluate(() =>
    document.querySelector('[data-present-body] .diff-block').textContent.length)
  expect(chars).toBeGreaterThan(700_000)
})
