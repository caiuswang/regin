/**
 * SessionTraceView: the INITIAL /map load has a failure exit (CAI-38).
 *
 * "Loading session…" is the view's only loading affordance, so a rejected
 * first `/api/sessions/<sid>/map` used to strand the view on it forever while
 * emitting an unhandled pageerror — no error surface, no retry.
 *
 * Covers:
 *  1. /map → 500: the loading state clears and an error surface with Retry renders.
 *  2. That failed load emits no unhandled pageerror.
 *  3. Retry after the failure lifts loads the session for real.
 *  4. Retry while /map still fails re-shows the error (no wedge, no double-load).
 *  5. Happy path: a healthy session renders with no error surface at all.
 *  6. The rendered detail is bounded — a huge 500 body is truncated, not dumped.
 *  7. The error surface takes focus, so a screen reader announces it (CAI-79).
 *
 * Plus the MID-SESSION counterpart (CAI-79): once the session has loaded, the
 * 4s live poll and useTraceScroll's pull-to-refresh call reload() unawaited, so
 * a /map that starts failing later used to throw an unhandled rejection every
 * tick while the frozen spans stayed on screen with nothing marking them stale.
 */
import { test, expect } from './auth-fixture.js'
import { contentOverflow } from './helpers/overflow.js'
import { randomUUID } from 'node:crypto'

const PROMPT_TEXT = 'CAI-38 load-failure fixture prompt'

const errorState = (page) => page.locator('[data-testid="trace-load-error"]')
const loadingState = (page) => page.getByText('Loading session…')
const retryBtn = (page) => errorState(page).getByRole('button', { name: 'Retry' })
const staleMarker = (page) => page.locator('[data-testid="trace-reload-error"]')

// `prompts: n` seeds n root prompts instead of one — enough of them (> the
// /map page size) is what arms `hasMoreOlder`, and therefore pull-older.
async function seedSession(page, { prompts = 0 } = {}) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  const spans = [
    {
      trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: PROMPT_TEXT, is_test: true },
    },
    {
      trace_id: traceId, span_id: `bash-${sfx}`, parent_id: `prompt-${sfx}`, name: 'tool.Bash',
      start_time: now,
      attributes: { command: 'echo CAI38', command_preview: 'echo CAI38', is_test: true },
    },
  ]
  for (let i = 0; i < prompts; i++) {
    spans.push({
      trace_id: traceId, span_id: `p${i}-${sfx}`, parent_id: null, name: 'prompt',
      start_time: new Date(Date.now() + (i + 1) * 1000).toISOString(),
      attributes: { text: `${PROMPT_TEXT} ${i}`, is_test: true },
    })
  }
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
  return traceId
}

// Fail the session-map request `times` times, then let it through. Returns a
// counter so a test can assert how many map fetches were actually issued.
async function failMap(page, traceId, { times = Infinity, body = 'boom: map exploded' } = {}) {
  const calls = { n: 0 }
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    calls.n += 1
    if (calls.n <= times) {
      await route.fulfill({ status: 500, contentType: 'text/plain', body })
      return
    }
    await route.continue()
  })
  return calls
}

function collectPageErrors(page) {
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  return errors
}

test.describe('Initial session load failure', () => {
  test('a 500 on /map clears the loading state and offers a retry, with no pageerror', async ({ page }) => {
    const traceId = await seedSession(page)
    const errors = collectPageErrors(page)
    await failMap(page, traceId)

    await page.goto(`/trace/sessions/${traceId}`)

    await expect(errorState(page)).toBeVisible({ timeout: 10_000 })
    await expect(retryBtn(page)).toBeVisible()
    await expect(loadingState(page)).toHaveCount(0)
    await expect(errorState(page)).toContainText('load this session')
    // The failure exit must not itself throw.
    expect(errors).toEqual([])
  })

  test('Retry loads the session once /map recovers', async ({ page }) => {
    const traceId = await seedSession(page)
    const errors = collectPageErrors(page)
    await failMap(page, traceId, { times: 1 })

    await page.goto(`/trace/sessions/${traceId}`)
    await expect(errorState(page)).toBeVisible({ timeout: 10_000 })

    await retryBtn(page).click()

    await expect(page.getByText(PROMPT_TEXT).first()).toBeVisible({ timeout: 10_000 })
    await expect(errorState(page)).toHaveCount(0)
    await expect(loadingState(page)).toHaveCount(0)
    expect(errors).toEqual([])
  })

  test('Retry that fails again re-shows the error instead of wedging', async ({ page }) => {
    const traceId = await seedSession(page)
    const errors = collectPageErrors(page)
    const calls = await failMap(page, traceId)

    await page.goto(`/trace/sessions/${traceId}`)
    await expect(errorState(page)).toBeVisible({ timeout: 10_000 })
    const afterFirst = calls.n

    await retryBtn(page).click()

    await expect(errorState(page)).toBeVisible({ timeout: 10_000 })
    await expect(loadingState(page)).toHaveCount(0)
    // One click, one map fetch — the pre-await guard must not admit a second.
    expect(calls.n).toBe(afterFirst + 1)
    expect(errors).toEqual([])
  })

  // Bound the detail on BOTH axes. A content-length cap alone still lets one
  // unbroken 200-char token (compact JSON, base64) push the app scroller
  // sideways, so assert the layout too.
  test('a long unbreakable 500 body is truncated and does not overflow sideways', async ({ page }) => {
    const traceId = await seedSession(page)
    await failMap(page, traceId, { body: `{"error":"${'x'.repeat(5000)}"}` })

    await page.goto(`/trace/sessions/${traceId}`)
    await expect(errorState(page)).toBeVisible({ timeout: 10_000 })

    const text = (await errorState(page).innerText()) || ''
    expect(text.length).toBeLessThan(400)

    const overflow = await contentOverflow(page)
    expect(overflow.scrollWidth, `offenders: ${overflow.offenders.join(', ')}`)
      .toBeLessThanOrEqual(overflow.clientWidth + 1)
  })

  test('a healthy session renders with no error surface', async ({ page }) => {
    const traceId = await seedSession(page)
    const errors = collectPageErrors(page)

    await page.goto(`/trace/sessions/${traceId}`)

    await expect(page.getByText(PROMPT_TEXT).first()).toBeVisible({ timeout: 10_000 })
    await expect(errorState(page)).toHaveCount(0)
    expect(errors).toEqual([])
  })

  // role="alert" alone is unreliable when the alert is inserted with its text
  // already in place — several screen readers stay silent. Focus is the fix.
  test('the failure surface takes focus so it is announced', async ({ page }) => {
    const traceId = await seedSession(page)
    await failMap(page, traceId)

    await page.goto(`/trace/sessions/${traceId}`)
    await expect(retryBtn(page)).toBeVisible({ timeout: 10_000 })

    await expect(retryBtn(page)).toBeFocused()
  })
})

test.describe('Mid-session reload failure', () => {
  // Two poll ticks' worth of a dead /map. Pre-fix this produced one unhandled
  // rejection per tick and left no trace of it on screen.
  const TWO_TICKS_MS = 11_000

  // Load the session for real, THEN start failing /map — the initial-load
  // failure path is a different surface and must not be the one under test.
  async function loadThenBreak(page, opts = {}) {
    const traceId = await seedSession(page)
    await page.goto(`/trace/sessions/${traceId}`)
    await expect(page.getByText(PROMPT_TEXT).first()).toBeVisible({ timeout: 10_000 })
    await expect(errorState(page)).toHaveCount(0)
    const calls = await failMap(page, traceId, opts)
    return { traceId, calls }
  }

  test('a poll that fails marks the header stale instead of throwing', async ({ page }) => {
    const errors = collectPageErrors(page)
    await loadThenBreak(page)

    await expect(staleMarker(page)).toBeVisible({ timeout: TWO_TICKS_MS })
    await page.waitForTimeout(TWO_TICKS_MS)

    // The whole point: unawaited reload() rejections are gone.
    expect(errors).toEqual([])
    // Degraded, not replaced — the loaded spans are still worth reading, and
    // the full-pane initial-load error would wrongly hide them.
    await expect(page.getByText(PROMPT_TEXT).first()).toBeVisible()
    await expect(errorState(page)).toHaveCount(0)
    await expect(loadingState(page)).toHaveCount(0)
  })

  test('the marker clears once /map recovers', async ({ page }) => {
    const errors = collectPageErrors(page)
    const { traceId } = await loadThenBreak(page)
    await expect(staleMarker(page)).toBeVisible({ timeout: TWO_TICKS_MS })

    await page.unroute(`**/api/sessions/${traceId}/map*`)

    await expect(staleMarker(page)).toHaveCount(0, { timeout: TWO_TICKS_MS })
    await expect(page.getByText(PROMPT_TEXT).first()).toBeVisible()
    expect(errors).toEqual([])
  })

  // The poll is not the only unawaited caller. Switching tabs, jumping to live
  // and pull-older each fire a /map-backed load from an event handler, and each
  // used to reject into nothing. Tab switch is the cheapest to drive.
  test('switching tabs during the outage degrades instead of throwing', async ({ page }) => {
    const errors = collectPageErrors(page)
    await loadThenBreak(page)

    await page.getByRole('button', { name: 'Terminal' }).click()
    await expect(staleMarker(page)).toBeVisible({ timeout: TWO_TICKS_MS })

    expect(errors).toEqual([])
  })

  // These two assert on the ACTION, not the marker: the 4s poll raises the
  // marker on its own, so a marker assertion here would pass even with the
  // action's own reject still escaping. The pageerror window is what bites.
  const ACTION_SETTLE_MS = 2_000

  test('jump-to-live during the outage degrades instead of throwing', async ({ page }) => {
    const errors = collectPageErrors(page)
    await loadThenBreak(page)

    await page.getByRole('button', { name: 'Jump to live' }).click()
    await page.waitForTimeout(ACTION_SETTLE_MS)

    expect(errors).toEqual([])
  })

  // Pull-older is the OTHER unawaited useTraceScroll callback, and it needs a
  // session deeper than one /map page before `hasMoreOlder` arms at all.
  test('pull-older during the outage degrades instead of throwing', async ({ page }) => {
    const errors = collectPageErrors(page)
    const traceId = await seedSession(page, { prompts: 60 })
    await page.goto(`/trace/sessions/${traceId}`)
    await expect(page.getByText(`${PROMPT_TEXT} 59`).first()).toBeVisible({ timeout: 15_000 })
    await failMap(page, traceId)

    // The view opens at scrollTop 0, so scrolling straight to the top is a
    // no-op that fires no scroll event at all — go down before coming back up.
    await page.evaluate(() => {
      const el = document.querySelector('.content-scroll')
      el?.scrollTo({ top: el.scrollHeight })
    })
    await page.waitForTimeout(500)
    await page.evaluate(() => {
      document.querySelector('.content-scroll')?.scrollTo({ top: 0 })
    })
    await page.waitForTimeout(ACTION_SETTLE_MS)

    expect(errors).toEqual([])
  })

  // Same both-axes bound as the initial-load detail: the header is a flex row
  // in the sticky chrome, so an unbroken 500 body rendered inline would widen
  // it on every tick. The detail rides the tooltip; the row stays fixed.
  test('a huge 500 body does not widen the header', async ({ page }) => {
    await loadThenBreak(page, { body: `{"error":"${'x'.repeat(5000)}"}` })
    await expect(staleMarker(page)).toBeVisible({ timeout: TWO_TICKS_MS })

    const text = (await staleMarker(page).innerText()) || ''
    expect(text.length).toBeLessThan(80)

    const overflow = await contentOverflow(page)
    expect(overflow.scrollWidth, `offenders: ${overflow.offenders.join(', ')}`)
      .toBeLessThanOrEqual(overflow.clientWidth + 1)
  })
})
