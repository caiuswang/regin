/**
 * Session-switcher sheet on the /live mobile card
 * (`frontend/src/components/live/LiveSessionPicker.vue`).
 *
 * Mirrors live-card.spec.js conventions: synthetic `is_test: true` sessions
 * posted via `/api/session-spans`, pinned 375x667 viewport, `settle()` +
 * `contentOverflow()` helpers.
 *
 * Note: the picker lists the recent-20 real DB sessions, so synthetic A/B
 * (posted just before navigation, hence newest) will be IN the list
 * alongside whatever else is in the dev DB — assertions only check for
 * A/B's presence, never an exact row count.
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

async function postAB(page) {
  const traceIdA = randomUUID()
  const traceIdB = randomUUID()
  const textA = `PICKER_FIXTURE_A_${traceIdA.slice(0, 8)}`
  const textB = `PICKER_FIXTURE_B_${traceIdB.slice(0, 8)}`
  await post(page, makeSession(traceIdA, textA))
  await post(page, makeSession(traceIdB, textB))
  return { traceIdA, traceIdB, textA, textB }
}

const rows = (page) => page.locator('[data-testid="live-picker-row"]')

test.describe('Session switcher (approved roadmap)', () => {
  test('switch button opens the sheet with >=2 session rows', async ({ page }) => {
    const { traceIdA } = await postAB(page)

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
    const { traceIdA } = await postAB(page)

    await page.goto(`/live/${traceIdA}`)
    await settle(page)
    await page.locator('[data-testid="live-switch"]').click()
    await expect(page.locator('[data-testid="live-sheet"]')).toBeVisible({ timeout: 5_000 })

    const currentRow = page.locator(`[data-testid="live-picker-row"][data-trace-id="${traceIdA}"]`)
    await expect(currentRow).toBeVisible()
    await expect(currentRow.locator('[data-testid="live-picker-current"]')).toBeVisible()
  })

  test('picking a different session navigates and re-inits the tail', async ({ page }) => {
    const { traceIdA, traceIdB, textA, textB } = await postAB(page)

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
    const { traceIdA } = await postAB(page)

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
    const { traceIdA } = await postAB(page)

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
})
