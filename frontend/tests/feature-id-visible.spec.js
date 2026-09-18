/**
 * The id a feature is addressed by, on the two pages that name the feature.
 *
 * Every declaration in the pipeline is `--feature <id>` — `regin defects open`,
 * `regin feature attribute`, `regin feature ship`. The browser held the id in
 * the URL and in nothing the reader could read, so the one string an operator
 * has to retype was the one the page would not show them.
 */
import { test, expect } from './auth-fixture.js'

function row(over = {}) {
  return {
    id: 242, title: 'Feature ledger', origin: 'native', external_ref: null,
    stage: 'shipped', blurb: 'completed and never walked back', rounds: 1,
    sessions: 3, tasks: 2, cost_usd: 41.9,
    gaps: { no_usage_captured: 0 },
    defects: { total: 0, open: 0, escapes: 0 },
    last_activity: '2026-09-01 10:00:00',
    ...over,
  }
}

function index(items) {
  return {
    items,
    pagination: {
      strategy: 'offset', total: items.length,
      page: 0, size: 50, has_next: false, has_prev: false,
    },
    total: 25,
    coverage: {
      declared_sessions: 13, all_sessions: 2935,
      declared_usd: 57.24, all_usd: 23251.49,
    },
    unattributed_defects: 0,
  }
}

function ledger(over = {}) {
  return {
    id: 242, title: 'Feature ledger', origin: 'native',
    external_ref: null, external_url: null, sync_error: null,
    stage: {
      stage: 'shipped', blurb: 'completed and never walked back',
      rounds: 1, claimed_at: '2026-09-13 05:40:00',
      open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true,
    cost: { usd: 41.9, sessions: 3, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 5_400_000, gaps: {} },
    tokens: { input: 1200, output: 3400 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: {
      declared_sessions: 13, all_sessions: 2935,
      declared_usd: 57.24, all_usd: 23251.49,
    },
    ...over,
  }
}

test('the index names each feature by its id', async ({ page }) => {
  await page.route(/\/api\/features(\?.*)?$/, (route) =>
    route.fulfill({ json: index([row(), row({ id: 206, title: 'Defect records' })]) }))
  await page.goto('/features')

  const ids = page.getByTestId('feature-id')
  await expect(ids).toHaveCount(2)
  await expect(ids.nth(0)).toHaveText('#242')
  await expect(ids.nth(1)).toHaveText('#206')
})

test('the id leads the ledger, beside the title it addresses',
  async ({ page }) => {
    await page.route(/\/api\/features\/[^/?]+$/, (route) =>
      route.fulfill({ json: ledger() }))
    await page.goto('/features/242')

    const id = page.getByTestId('feature-id')
    await expect(id).toHaveText('#242')
    await expect(page.getByTestId('feature-id')).toBeVisible()
  })

// A tracker reference is where the feature is tracked; the id is what every
// regin command takes. One must not stand in for the other.
test('a remotely tracked feature shows its id as well as its reference',
  async ({ page }) => {
    await page.route(/\/api\/features\/[^/?]+$/, (route) =>
      route.fulfill({ json: ledger({
        origin: 'linear', external_ref: 'CAI-274',
        external_url: 'https://linear.app/x/issue/CAI-274', stage_live: false,
      }) }))
    await page.goto('/features/242')

    await expect(page.getByTestId('feature-id')).toHaveText('#242')
    await expect(page.getByTestId('stage-ref')).toHaveText('CAI-274')
  })
