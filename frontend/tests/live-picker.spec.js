/**
 * Session-switcher sheet on the /live mobile card
 * (`frontend/src/components/live/LiveSessionPicker.vue`).
 *
 * Mirrors live-card.spec.js conventions: synthetic `is_test: true` sessions
 * posted via `/api/session-spans`, pinned 375x667 viewport, `settle()` +
 * `contentOverflow()` helpers.
 *
 * The picker's list request is `kind=real` — test/synthetic sessions are
 * excluded from the switcher by design (that's the bug this file guards
 * against, see the last test). So for the list-rendering tests below we
 * `page.route('**\/api/sessions?kind=real*', …)` to fulfill a deterministic
 * two-row fixture (A/B) shaped like the real endpoint's response — sampled
 * via a live `curl`/`page.request.get` against the dev server — rather than
 * relying on whatever real sessions happen to be in the dev DB. The PAGE
 * itself still navigates to a real synthetic session posted via
 * /api/session-spans (as before) so the tail content renders; only the
 * PICKER's list is reshaped. Where a test actually navigates to row B (the
 * "select navigates" case), B is a real posted synthetic session too, with
 * its real trace_id substituted into the mocked list, so /live/<B> has rows
 * to load after the switch.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { contentOverflow, settle } from './helpers/overflow.js'

test.use({ viewport: { width: 375, height: 667 } })

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

function makeSession(traceId, promptText) {
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  return [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: promptText, is_test: true } },
    { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
      start_time: now, attributes: { text: `${promptText} — response`, is_test: true } },
  ]
}

async function postSession(page, prefix) {
  const traceId = randomUUID()
  const text = `PICKER_FIXTURE_${prefix}_${traceId.slice(0, 8)}`
  await post(page, makeSession(traceId, text))
  return { traceId, text }
}

// Row shape copied from a live GET /api/sessions?kind=real response — only
// the fields the picker actually reads (trace_id, title, status, last_seen).
function fakeRow(traceId, title, status) {
  const now = new Date().toISOString()
  return { trace_id: traceId, title, status, last_seen: now, started_at: now, is_test: 0 }
}

async function mockPickerList(page, rows) {
  await page.route('**/api/sessions?kind=real*', (route) =>
    route.fulfill({ json: { sessions: rows, items: rows, cursor: null, has_more: false } })
  )
}

const rows = (page) => page.locator('[data-testid="live-picker-row"]')

test.describe('Session switcher (approved roadmap)', () => {
  test('switch button opens the sheet with >=2 session rows', async ({ page }) => {
    const { traceId: traceIdA } = await postSession(page, 'A')
    const traceIdB = randomUUID()
    await mockPickerList(page, [
      fakeRow(traceIdA, 'Fixture A', 'active'),
      fakeRow(traceIdB, 'Fixture B', 'ended'),
    ])

    await page.goto(`/live/${traceIdA}`)
    await settle(page)

    const switchBtn = page.locator('[data-testid="live-switch"]')
    await expect(switchBtn).toBeVisible({ timeout: 10_000 })
    await switchBtn.click()

    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible({ timeout: 5_000 })
    await expect(rows(page)).not.toHaveCount(0)
    expect(await rows(page).count()).toBeGreaterThanOrEqual(2)
  })

  test('current session row carries the check marker', async ({ page }) => {
    const { traceId: traceIdA } = await postSession(page, 'A')
    const traceIdB = randomUUID()
    await mockPickerList(page, [
      fakeRow(traceIdA, 'Fixture A', 'active'),
      fakeRow(traceIdB, 'Fixture B', 'ended'),
    ])

    await page.goto(`/live/${traceIdA}`)
    await settle(page)
    await page.locator('[data-testid="live-switch"]').click()
    await expect(page.locator('[data-testid="live-sheet"]')).toBeVisible({ timeout: 5_000 })

    const currentRow = page.locator(`[data-testid="live-picker-row"][data-trace-id="${traceIdA}"]`)
    await expect(currentRow).toBeVisible()
    await expect(currentRow.locator('[data-testid="live-picker-current"]')).toBeVisible()
  })

  test('picking a different session navigates and re-inits the tail', async ({ page }) => {
    const { traceId: traceIdA, text: textA } = await postSession(page, 'A')
    const { traceId: traceIdB, text: textB } = await postSession(page, 'B')
    await mockPickerList(page, [
      fakeRow(traceIdA, 'Fixture A', 'active'),
      fakeRow(traceIdB, 'Fixture B', 'active'),
    ])

    await page.goto(`/live/${traceIdA}`)
    await settle(page)
    await expect(page.getByText(textA).first()).toBeVisible({ timeout: 10_000 })

    await page.locator('[data-testid="live-switch"]').click()
    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible({ timeout: 5_000 })

    const rowB = page.locator(`[data-testid="live-picker-row"][data-trace-id="${traceIdB}"]`)
    await expect(rowB).toBeVisible()
    await rowB.click()

    await expect(page).toHaveURL(new RegExp('/live/' + traceIdB))
    await expect(sheet).toBeHidden({ timeout: 5_000 })
    await expect(page.getByText(textB).first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText(textA)).toHaveCount(0)
  })

  test('picking the current session just closes the sheet, no navigation', async ({ page }) => {
    const { traceId: traceIdA } = await postSession(page, 'A')
    const traceIdB = randomUUID()
    await mockPickerList(page, [
      fakeRow(traceIdA, 'Fixture A', 'active'),
      fakeRow(traceIdB, 'Fixture B', 'ended'),
    ])

    await page.goto(`/live/${traceIdA}`)
    await settle(page)
    await page.locator('[data-testid="live-switch"]').click()
    const sheet = page.locator('[data-testid="live-sheet"]')
    await expect(sheet).toBeVisible({ timeout: 5_000 })

    const urlBefore = page.url()
    const currentRow = page.locator(`[data-testid="live-picker-row"][data-trace-id="${traceIdA}"]`)
    await currentRow.click()

    await expect(sheet).toBeHidden({ timeout: 5_000 })
    expect(page.url()).toBe(urlBefore)
  })

  test('375px: no horizontal overflow while the sheet is open', async ({ page }) => {
    const { traceId: traceIdA } = await postSession(page, 'A')
    const traceIdB = randomUUID()
    await mockPickerList(page, [
      fakeRow(traceIdA, 'Fixture A', 'active'),
      fakeRow(traceIdB, 'Fixture B', 'ended'),
    ])

    await page.goto(`/live/${traceIdA}`)
    await settle(page)
    await page.locator('[data-testid="live-switch"]').click()
    await expect(page.locator('[data-testid="live-sheet"]')).toBeVisible({ timeout: 5_000 })
    await expect(rows(page).first()).toBeVisible()

    const m = await contentOverflow(page)
    test.skip(!m.pane, 'content pane not present (redirected to login/other)')
    expect(
      m.scrollWidth,
      `/live session picker overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ') || 'unknown'}`
    ).toBeLessThanOrEqual(m.clientWidth + 1)
  })

  test('regression: TEST sessions are excluded from the switcher list', async ({ page }) => {
    // No interception here — this exercises the real /api/sessions endpoint
    // to guard the actual bug: TEST (Playwright-fixture) sessions must never
    // show up in the switcher, even though a direct /live/<id> deep-link to
    // one still works (useLiveTail's own kind=all row lookup).
    const { traceId: testTraceId } = await postSession(page, 'REGRESSION')

    const pickerRequestUrls = []
    page.on('request', (req) => {
      const url = req.url()
      // size=20 is unique to the picker's list fetch — useLiveTail's own
      // meta lookup (kind=all&size=1) hits the same endpoint and must not
      // be conflated with it.
      if (url.includes('/api/sessions?') && url.includes('size=20')) {
        pickerRequestUrls.push(url)
      }
    })

    await page.goto(`/live/${testTraceId}`)
    await settle(page)
    await page.locator('[data-testid="live-switch"]').click()
    await expect(page.locator('[data-testid="live-sheet"]')).toBeVisible({ timeout: 5_000 })
    await page.waitForTimeout(300)

    expect(pickerRequestUrls.length).toBeGreaterThan(0)
    for (const url of pickerRequestUrls) {
      expect(url).toContain('kind=real')
    }

    const testRow = page.locator(`[data-testid="live-picker-row"][data-trace-id="${testTraceId}"]`)
    await expect(testRow).toHaveCount(0)
  })
})
