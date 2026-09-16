/**
 * The feature index and its search (CAI-248, CAI-255).
 *
 * Stubbed, because every assertion is about what a row and a counter claim —
 * and the most important row to render is the one with nothing in it, which a
 * live database would not reliably supply.
 */
import { test, expect } from './auth-fixture.js'

function row(over = {}) {
  return {
    key: 'CAI-242', title: 'Feature ledger', stage: 'shipped',
    stage_synced_at: '2026-09-13 05:40:00', sessions: 3, cost_usd: 41.9,
    gaps: { no_usage_captured: 0 },
    defects: { total: 0, open: 0, escapes: 0 },
    last_activity: '2026-09-01 10:00:00',
    ...over,
  }
}

// A key that exists only because a bug was blamed on it: no stage, no session,
// no cost. Measured, this is a fifth of the real population, and it is the
// population this surface exists to shrink.
const blamedOnly = row({
  key: 'CAI-206', title: null, stage: null, stage_synced_at: null,
  sessions: 0, cost_usd: null,
  defects: { total: 2, open: 1, escapes: 0 },
  last_activity: '2026-08-01 10:00:00',
})

function index(over = {}) {
  return {
    items: [row(), blamedOnly],
    total: 25,
    coverage: {
      declared_sessions: 13, all_sessions: 2935,
      declared_usd: 57.24, all_usd: 23251.49,
    },
    unattributed_defects: 1,
    ...over,
  }
}

async function stub(page, body) {
  await page.route(/\/api\/features(\?.*)?$/, (route) =>
    route.fulfill({ json: body }))
}

test('a row with no stage still renders, and is not degraded', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  const rows = page.locator('tbody tr')
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(1)).toContainText('stage not fetched')
  await expect(rows.nth(1)).toContainText('none declared')
  await expect(rows.nth(1)).not.toContainText('$0.00')
})

test('the cost column carries its bound', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  await expect(page.locator('tbody tr').first()).toContainText('≤ $41.90')
})

test('the header counters describe the population, not the match',
  async ({ page }) => {
    await stub(page, index({ items: [row()] }))
    await page.goto('/features?q=ledger')

    await expect(page.getByText('1 of 25 features')).toBeVisible()
  })

test('with no search the count is the plain population', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  await expect(page.getByText('25 features', { exact: true })).toBeVisible()
})

test('the unattributed-defect counter is a count, not a link', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  const counter = page.locator('.counter-gap')
  await expect(counter).toContainText('1')
  await expect(counter).toContainText('blamed on no feature')
  await expect(counter.locator('a')).toHaveCount(0)
  await expect(counter.locator('button')).toHaveCount(0)
})

test('search submits on Enter and lands in the URL', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  const input = page.getByLabel('Search features by key or title')
  await input.fill('ledger')
  // Typing alone must not navigate: a debounce would be a timer heuristic,
  // and a submit is a navigation.
  await expect(page).toHaveURL(/\/features$/)
  await input.press('Enter')

  await expect(page).toHaveURL(/\/features\?q=ledger$/)
})

test('no match names the population rather than showing nothing',
  async ({ page }) => {
    await stub(page, index({ items: [] }))
    await page.goto('/features?q=zzz')

    await expect(page.locator('.empty')).toContainText('no match')
    await expect(page.locator('.empty')).toContainText('25 feature keys')
  })

test('the key links into its ledger', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  await expect(page.locator('.key').first())
    .toHaveAttribute('href', '/features/CAI-242')
})
