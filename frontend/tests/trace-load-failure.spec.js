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
 */
import { test, expect } from './auth-fixture.js'
import { contentOverflow } from './helpers/overflow.js'
import { randomUUID } from 'node:crypto'

const PROMPT_TEXT = 'CAI-38 load-failure fixture prompt'

const errorState = (page) => page.locator('[data-testid="trace-load-error"]')
const loadingState = (page) => page.getByText('Loading session…')
const retryBtn = (page) => errorState(page).getByRole('button', { name: 'Retry' })

async function seedSession(page) {
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
})
