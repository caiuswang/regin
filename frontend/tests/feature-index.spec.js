/**
 * The feature index (feature #110's stage-track table) and its controls.
 *
 * Stubbed, because every assertion is about what a row claims — and the most
 * important row to render is the one with nothing in it, which a live database
 * would not reliably supply. The narrowing assertions are URL assertions: every
 * control writes its axis into `location.search` and the request carries it.
 */
import { test, expect } from './auth-fixture.js'

function row(over = {}) {
  return {
    id: 242, title: 'Feature ledger', origin: 'native', external_ref: null,
    stage: 'shipped', blurb: '', rounds: 1, sessions: 3, tasks: 2, tasks_done: 1,
    cost_usd: 41.9, gaps: { no_usage_captured: 0 },
    defects: { total: 0, open: 0, escapes: 0 },
    last_activity: '2026-09-01 10:00:00',
    ...over,
  }
}

// A feature nobody has run: no task, no cost, and a stage anyway.
const untouched = row({
  id: 206, title: 'Defect records', stage: 'spec', sessions: 0, tasks: 0,
  tasks_done: 0, cost_usd: null, rounds: 4,
  defects: { total: 2, open: 1, escapes: 0 },
  last_activity: '2026-08-01 10:00:00',
})

function index(over = {}) {
  const items = over.items || [row(), untouched]
  return {
    items,
    pagination: { strategy: 'offset', total: items.length, page: 0, size: 50,
                  has_next: false, has_prev: false },
    population: 25,
    extras: { stage_counts: { all: 25, spec: 5, building: 7, shipped: 10, escaped: 3 },
              unattributed_defects: 0, attribution: null },
    ...over,
  }
}

async function stub(page, body) {
  const seen = []
  await page.route(/\/api\/features(\?.*)?$/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const params = new URL(route.request().url()).searchParams
    if (params.get('size') === '200') return route.fulfill({ json: index() })
    seen.push(params)
    // The page renders the sort the server APPLIED, so the stub echoes it.
    const applied = { sort: params.get('sort') || 'activity:desc', filters: {} }
    return route.fulfill({ json: { ...body, applied } })
  })
  return seen
}

const last = (seen) => seen[seen.length - 1]
const rows = (page) => page.locator('[data-testid^="feature-row-"]')

test.beforeEach(async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
})

test('every row carries a real stage, and a costless one is not a zero',
  async ({ page }) => {
    await stub(page, index())
    await page.goto('/features')

    await expect(rows(page)).toHaveCount(2)
    await expect(rows(page).nth(1)).toContainText('Spec1 of 3')
    await expect(rows(page).nth(1)).toContainText('none declared')
    await expect(rows(page).nth(1)).not.toContainText('$0.00')
  })

test('the cost column carries its bound', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')
  await expect(rows(page).first()).toContainText('≤ $41.90')
})

// Rounds-to-done is the figure this whole pipeline exists to count.
test('rounds is a column', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  await expect(page.getByTestId('feature-table')).toContainText('Rounds')
  const rounds = page.getByTestId('feature-rounds')
  await expect(rounds.nth(0)).toHaveAttribute('aria-label', '1 rounds')
  await expect(rounds.nth(1)).toHaveAttribute('aria-label', '4 rounds')
  await expect(rounds.nth(1).locator('span')).toHaveCount(4)
})

test('search submits on Enter and lands in the URL', async ({ page }) => {
  const seen = await stub(page, index())
  await page.goto('/features')

  const input = page.getByLabel('Search features by title, spec or tracker reference')
  await input.fill('ledger')
  // Typing alone must not navigate: a debounce would be a timer heuristic,
  // and a submit is a navigation.
  await expect(page).toHaveURL(/\/features$/)
  await input.press('Enter')

  await expect(page).toHaveURL(/\/features\?q=ledger$/)
  await expect.poll(() => last(seen).get('q')).toBe('ledger')
})

test('a stage tab writes the URL and the request', async ({ page }) => {
  const seen = await stub(page, index())
  await page.goto('/features')

  await page.getByTestId('feature-stage-building').click()
  await expect(page).toHaveURL(/stage=building/)
  await expect.poll(() => last(seen).get('stage')).toBe('building')
  await expect(page.getByTestId('feature-stage-building')).toHaveAttribute('aria-selected', 'true')
})

test('the sort control writes the URL and the request', async ({ page }) => {
  const seen = await stub(page, index())
  await page.goto('/features')

  await expect(page.getByTestId('features-sort')).toContainText('Newest')
  await page.getByTestId('features-sort').click()
  await page.getByRole('option', { name: 'Rounds ↓' }).click()
  await expect(page).toHaveURL(/sort=rounds(%3A|:)desc/)
  await expect.poll(() => last(seen).get('sort')).toBe('rounds:desc')
})

test('the pager asks the server for the next page', async ({ page }) => {
  const seen = await stub(page, index({
    pagination: { strategy: 'offset', total: 120, page: 0, size: 50,
                  has_next: true, has_prev: false },
  }))
  await page.goto('/features')

  await expect(page.getByText('1–50 of 120')).toBeVisible()
  await page.getByRole('button', { name: 'Next' }).click()

  await expect(page).toHaveURL(/page=1/)
  await expect.poll(() => last(seen).get('page')).toBe('1')
})

test('the title links into its ledger', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')
  await expect(rows(page).first().getByRole('link', { name: 'Feature ledger' }))
    .toHaveAttribute('href', '/features/242')
})

test('a remote feature shows its reference beside the title, not instead of it',
  async ({ page }) => {
    await stub(page, index({
      items: [row({ origin: 'linear', external_ref: 'CAI-274' })],
    }))
    await page.goto('/features')

    await expect(rows(page).first()).toContainText('Feature ledger')
    await expect(rows(page).first()).toContainText('CAI-274')
  })

test('an unsubmitted search term does not survive another control',
  async ({ page }) => {
    await stub(page, index())
    await page.goto('/features?q=ledger')

    const input = page.getByLabel('Search features by title, spec or tracker reference')
    await expect(input).toHaveValue('ledger')
    await input.fill('never submitted')

    await page.getByTestId('feature-stage-building').click()
    await expect(page).toHaveURL(/q=ledger/)
    await expect(input).toHaveValue('ledger')
  })

test('the task count opens the tasks it counted', async ({ page }) => {
  await stub(page, index({ items: [row()] }))
  const asked = []
  await page.route(/\/api\/tasks(\/counts)?\?/, (route) => {
    asked.push(new URL(route.request().url()).searchParams)
    return route.fulfill({ json: {
      items: [], pagination: { has_next: false },
      total: 2, by_status: {}, by_priority: {}, by_project: {},
    } })
  })

  await page.goto('/features')
  await page.getByTestId('feature-tasks-link-242').click()

  await expect(page).toHaveURL(/\/tasks\?feature_id=242/)
  await expect(page.getByTestId('tasks-scoped-to')).toBeVisible()
  expect(asked.some(q => q.get('feature_id') === '242')).toBe(true)
  // Arriving by a link wins over a filter left on from yesterday.
  expect(asked.every(q => !q.get('status'))).toBe(true)
})

test('the defect count opens this feature\'s own defect list', async ({ page }) => {
  await stub(page, index({ items: [untouched] }))
  await page.goto('/features')

  await expect(page.getByTestId('feature-defects-link-206'))
    .toHaveAttribute('href', '/features/206#feature-defects')
})

test('a count of nothing is not a link', async ({ page }) => {
  await stub(page, index({ items: [untouched, row({ id: 7, defects: { total: 0, open: 0, escapes: 0 } })] }))
  await page.goto('/features')

  await expect(page.getByTestId('feature-tasks-link-206')).toHaveCount(0)
  await expect(page.getByTestId('feature-row-206')).toContainText('none')
  await expect(page.getByTestId('feature-defects-link-7')).toHaveCount(0)
})

test('a row names its next step and links to it', async ({ page }) => {
  await stub(page, index({ items: [
    row({ stage: 'escaped',
          next_step: { kind: 'reopen', label: '2 blamed defects — reopen to fix',
                       target: '/features/242' } }),
    row({ id: 206, stage: 'shipped', next_step: { kind: 'none', label: '', target: null } }),
  ] }))
  await page.goto('/features')

  const step = page.getByTestId('feature-row-242').getByTestId('feature-next-step')
  await expect(step).toHaveText('Next: 2 blamed defects — reopen to fix')
  await expect(step).toHaveAttribute('href', '/features/242')
  await expect(page.getByTestId('feature-row-206').getByTestId('feature-next-step'))
    .toHaveCount(0)
})
