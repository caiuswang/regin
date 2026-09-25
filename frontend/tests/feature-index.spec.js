/**
 * The feature index and its controls.
 *
 * Stubbed, because every assertion is about what a row and a counter claim —
 * and the most important row to render is the one with nothing in it, which a
 * live database would not reliably supply.
 *
 * The narrowing assertions are all URL assertions. `GET /api/features` is an
 * offset page with facets and seven sort keys, so nothing here filters or
 * sorts a loaded array: what the page must get right is that every control
 * writes its axis into `location.search` and that the request carries it.
 */
import { test, expect } from './auth-fixture.js'

function row(over = {}) {
  return {
    id: 242, title: 'Feature ledger', origin: 'native', external_ref: null,
    stage: 'shipped', blurb: 'completed and never walked back', rounds: 1,
    sessions: 3, cost_usd: 41.9,
    gaps: { no_usage_captured: 0 },
    defects: { total: 0, open: 0, escapes: 0 },
    last_activity: '2026-09-01 10:00:00',
    ...over,
  }
}

// A feature nobody has run: no session, no cost, and a stage anyway. It used
// to have none at all — the stage needed a tracker fetch, so a fifth of the
// real population rendered "stage not fetched".
const untouched = row({
  id: 206, title: 'Defect records', stage: 'spec',
  sessions: 0, cost_usd: null, rounds: 4,
  defects: { total: 2, open: 1, escapes: 0 },
  last_activity: '2026-08-01 10:00:00',
})

function index(over = {}) {
  const items = over.items || [row(), untouched]
  return {
    items,
    pagination: {
      strategy: 'offset', total: over.matched ?? items.length,
      page: 0, size: 50, has_next: false, has_prev: false,
    },
    total: 25,
    coverage: {
      declared_sessions: 13, all_sessions: 2935,
      declared_usd: 57.24, all_usd: 23251.49,
    },
    unattributed_defects: 1,
    ...over,
  }
}

// Captures every index request so a control can be checked by the query it
// sent, not only by the URL it wrote.
async function stub(page, body) {
  const seen = []
  await page.route(/\/api\/features(\?.*)?$/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    seen.push(new URL(route.request().url()).searchParams)
    return route.fulfill({ json: body })
  })
  return seen
}

const last = (seen) => seen[seen.length - 1]

test('every row carries a real stage, and a costless one is not a zero',
  async ({ page }) => {
    await stub(page, index())
    await page.goto('/features')

    const rows = page.locator('tbody tr')
    await expect(rows).toHaveCount(2)
    await expect(rows.nth(1)).toContainText('spec')
    await expect(rows.nth(1)).not.toContainText('stage not fetched')
    await expect(rows.nth(1)).toContainText('none declared')
    await expect(rows.nth(1)).not.toContainText('$0.00')
  })

test('the cost column carries its bound', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  await expect(page.locator('tbody tr').first()).toContainText('≤ $41.90')
})

// Rounds-to-done is the figure this whole pipeline exists to count, and it
// rode in the payload unrendered.
test('rounds is a column', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  await expect(page.locator('thead')).toContainText('Rounds')
  const rounds = page.getByTestId('feature-rounds')
  await expect(rounds.nth(0)).toHaveText('1')
  await expect(rounds.nth(1)).toHaveText('4')
})

test('the header counters describe the population, not the match',
  async ({ page }) => {
    await stub(page, index({ items: [row()] }))
    await page.goto('/features?q=ledger')

    await expect(page.getByText('1 of 25 features match')).toBeVisible()
  })

test('with no search the count is the plain population', async ({ page }) => {
  await stub(page, index())
  // Since #73 the tab counts are the shell's, from one scoped request — the
  // features endpoint's own total no longer captions the tab.
  await page.route('**/api/pipeline/counts*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json',
                    body: JSON.stringify({ counts: { tasks: 4, features: 25, defects: 1 } }) }))
  await page.goto('/features')

  // On the Pipeline tab since #70 — the page states the population once, and
  // the in-page line is now only ever the match's "N of M".
  await expect(page.getByTestId('pipeline-tab-features')).toContainText('25')
  await expect(page.getByText('features match')).toHaveCount(0)
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

test('the stage facet writes the URL and the request', async ({ page }) => {
  const seen = await stub(page, index())
  await page.goto('/features')

  await page.getByLabel('Filter by stage').click()
  await page.getByRole('option', { name: 'building' }).click()

  await expect(page).toHaveURL(/stage=building/)
  await expect.poll(() => last(seen).get('stage')).toBe('building')
})

test('a column header is a sort button carrying aria-sort', async ({ page }) => {
  const seen = await stub(page, index())
  await page.goto('/features')

  // The default order is newest-created first, announced by the toolbar
  // (no column carries `created`), so no header claims it.
  await expect.poll(() => last(seen)?.get('sort')).toBe('created:desc')
  await expect(page.getByLabel('Sort by')).toContainText('Created')
  const activity = page.locator('th', { has: page.getByTestId('sort-activity') })
  await expect(activity).toHaveAttribute('aria-sort', 'none')
  // A column that cannot be sorted wears no affordance at all.
  const feature = page.locator('thead th').first()
  await expect(feature).toHaveAttribute('aria-sort', 'none')
  await expect(feature.locator('button')).toHaveCount(0)

  await page.getByTestId('sort-rounds').click()

  await expect(page).toHaveURL(/sort=rounds%3Adesc|sort=rounds:desc/)
  await expect.poll(() => last(seen).get('sort')).toBe('rounds:desc')
  const roundsHeader = page.locator('th', { has: page.getByTestId('sort-rounds') })
  await expect(roundsHeader).toHaveAttribute('aria-sort', 'descending')
  await expect(activity).toHaveAttribute('aria-sort', 'none')
})

test('clicking the active column reverses it', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features?sort=rounds:desc')

  await page.getByTestId('sort-rounds').click()

  await expect(page).toHaveURL(/sort=rounds(%3A|:)asc/)
  await expect(page.locator('th', { has: page.getByTestId('sort-rounds') }))
    .toHaveAttribute('aria-sort', 'ascending')
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

test('a filter change drops the page it no longer describes', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features?page=3')

  await page.getByLabel('Filter by stage').click()
  await page.getByRole('option', { name: 'shipped' }).click()

  await expect(page).toHaveURL(/stage=shipped/)
  await expect(page).not.toHaveURL(/page=/)
})

test('reset clears every narrowing at once', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features?q=ledger&stage=shipped&sort=rounds:asc&page=2')

  await page.getByTestId('toolbar-reset').click()

  await expect(page).toHaveURL(/\/features$/)
})

test('reset is disabled when there is nothing to undo', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  await expect(page.getByTestId('toolbar-reset')).toBeDisabled()
})

test('no match names the population rather than showing nothing',
  async ({ page }) => {
    await stub(page, index({ items: [], matched: 0 }))
    await page.goto('/features?q=zzz')

    await expect(page.locator('.empty')).toContainText('no match')
    await expect(page.locator('.empty')).toContainText('25 features')
  })

test('the title links into its ledger', async ({ page }) => {
  await stub(page, index())
  await page.goto('/features')

  await expect(page.locator('.key').first())
    .toHaveAttribute('href', '/features/242')
})

// `tr:hover` promised a hit target the whole row did not have: only the title
// was clickable, so every click aimed at the stage or the cost landed nowhere.
test('the whole row is the hit target', async ({ page }) => {
  await stub(page, index())
  await page.route(/\/api\/features\/242$/, (route) =>
    route.fulfill({ status: 404, json: { error: 'stubbed' } }))
  await page.goto('/features')

  // The cost cell — as far from the title link as the row goes.
  await page.locator('tbody tr').first().locator('td').nth(4).click()

  await expect(page).toHaveURL(/\/features\/242$/)
})

test('a link inside the row keeps its own destination', async ({ page }) => {
  await stub(page, index({ items: [untouched] }))
  await page.goto('/features')

  await page.locator('tbody tr').first().getByRole('link', { name: '1 / 2' })
    .click()

  await expect(page).toHaveURL(/\/defects\?feature_id=206/)
})

test('a remote feature shows its reference beside the title, not instead of it',
  async ({ page }) => {
    await stub(page, index({
      items: [row({ origin: 'linear', external_ref: 'CAI-274' })],
    }))
    await page.goto('/features')

    const first = page.locator('tbody tr').first()
    await expect(first).toContainText('Feature ledger')
    await expect(first).toContainText('CAI-274')
  })

// An offset past the end is a real read that returns no rows — the server
// echoes the page it was asked for. Hiding the pager with the table is how a
// reader following a stale link gets stranded with no way back.
test('a page past the end keeps its pager and names its own cause',
  async ({ page }) => {
    await stub(page, index({
      items: [],
      pagination: { strategy: 'offset', total: 120, page: 9, size: 50,
                    has_next: false, has_prev: true },
    }))
    await page.goto('/features?page=9')

    await expect(page.locator('.empty')).toContainText('past the end')
    await expect(page.locator('.empty')).not.toContainText('no match')
    // Clamped, because Prev from page 10 would otherwise walk back one empty
    // page at a time from wherever the link pointed.
    await page.getByRole('button', { name: 'Prev' }).click()
    await expect(page).toHaveURL(/page=2$/)
  })

// The box must never describe rows other than the ones below it.
test('an unsubmitted search term does not survive another control',
  async ({ page }) => {
    await stub(page, index())
    await page.goto('/features?q=ledger')

    const input = page.getByLabel('Search features by title, spec or tracker reference')
    await expect(input).toHaveValue('ledger')
    await input.fill('never submitted')

    await page.getByLabel('Filter by stage').click()
    await page.getByRole('option', { name: 'building' }).click()

    await expect(page).toHaveURL(/q=ledger/)
    await expect(input).toHaveValue('ledger')
  })
