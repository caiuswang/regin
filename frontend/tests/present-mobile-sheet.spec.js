/**
 * Below lg the presentation reader is the full-content surface, and a tap on a
 * row means "expand this one". Neither may raise the span-detail bottom sheet:
 * over the feed it is a dismissal per read, and under the reader it is a modal
 * the user has to dismiss a second time. The `?span=` deep link — which names
 * one span and asks for nothing else — is the one route that still opens it.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test.use({ viewport: { width: 390, height: 844 } })

const NEEDLE = 'zqxjpresentsheet'

const BODY = [
  `## A response long enough to be worth reading ${NEEDLE}`,
  '',
  'It needs more than the 320-character clamp so the feed folds it and the',
  'reader has something the card does not already show in full.',
].join('\n')

async function seed(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const spans = [
    {
      trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-08T12:00:00', attributes: { text: 'seeded prompt', is_test: true },
    },
    {
      trace_id: traceId, span_id: `s1-${p8}`, parent_id: prompt, name: 'assistant_response',
      start_time: '2026-05-08T12:00:01', duration_ms: 4200,
      attributes: { text: BODY, is_test: true },
    },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return { traceId, spanId: `s1-${p8}` }
}

const reader = (page) => page.getByTestId('span-present-reader')
// A CSS attribute selector, not getByRole: the reader is a modal dialog, and
// under it the whole document drops out of the accessibility tree — a role
// query reports the stacked sheet as absent exactly when it is the defect.
const sheet = (page) => page.locator('[aria-label="Close span details"]')
const row = (page) => page.locator('.event-spine-row').filter({ hasText: 'Assistant' }).first()
const PANEL = '[role="dialog"][aria-label="Full-text search"]'

test('the ⤢ button opens the reader alone, not the detail sheet under it', async ({ page }) => {
  await seed(page)
  await expect(sheet(page)).toHaveCount(0)

  await row(page).locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await expect(sheet(page)).toHaveCount(0)

  // And it is not merely hidden under the reader, waiting to be dismissed a
  // second time once the reader closes.
  await page.keyboard.press('Escape')
  await expect(reader(page)).toHaveCount(0)
  await expect(sheet(page)).toHaveCount(0)
})

test('a card tap selects the row without raising the sheet', async ({ page }) => {
  await seed(page)
  await row(page).locator('p').first().click()
  await page.waitForTimeout(500)
  await expect(sheet(page)).toHaveCount(0)
})

test('a ?span= deep link still opens the sheet', async ({ page }) => {
  const { traceId, spanId } = await seed(page)
  await page.goto(`/trace/sessions/${traceId}?view=conversation&span=${spanId}`)
  await expect(sheet(page)).toBeVisible({ timeout: 20_000 })
})

test('opening a search hit opens the reader alone', async ({ page }) => {
  await seed(page)
  await page.keyboard.press('Meta+k')
  await page.waitForSelector(PANEL)
  await page.locator(`${PANEL} .query-input`).fill(NEEDLE)
  const hit = page.locator(`${PANEL} [role="option"]`).filter({ hasText: 'assistant_response' }).first()
  await hit.waitFor({ timeout: 15_000 })
  // Narrow: the first ↵ reveals the preview sheet, the second opens the span.
  await hit.click()
  await page.keyboard.press('Enter')

  await expect(reader(page)).toBeVisible()
  await expect(sheet(page)).toHaveCount(0)
})
