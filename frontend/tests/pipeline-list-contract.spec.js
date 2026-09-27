/**
 * Feature #73 · R1 + R3 — the two list rules that are only true in a browser.
 *
 * `test_filter_change_resets_page_and_replaces_history` is one acceptance item
 * carrying two claims, and both are about navigation rather than about rows:
 * an offset that survives a narrowing points into a list that no longer has
 * those rows, and a filter history is a stack nobody asked to keep — Back has
 * to leave the page, not walk the last six controls. Neither is observable
 * from a request.
 *
 * Every list is stubbed. What is under test is the query the page sends and
 * the address it leaves behind, never the ledger.
 */
import { test, expect } from './auth-fixture.js'

const COUNTS = { tasks: 7, features: 3, defects: 5 }

function defect(over = {}) {
  return {
    id: 1, feature_id: null, feature_title: null, symptom: 'the gutter folds',
    status: 'open', round: 1, blamed_against_state: null, missing_check: null,
    suspect_paths: [], fix_provider: null, fix_external_key: null,
    opened_at: '2026-09-14 12:00:00', closed_at: null, ...over,
  }
}
const BLAMED = defect({ id: 2, feature_id: 81, feature_title: 'The ledger',
                        symptom: 'the rail clips',
                        blamed_against_state: 'started' })
const LOOSE = defect()

const FEATURE = {
  id: 242, title: 'Feature ledger', origin: 'native', external_ref: null,
  stage: 'shipped', blurb: '', rounds: 1, sessions: 1, cost_usd: 1,
  gaps: {}, defects: { total: 0, open: 0, escapes: 0 },
  last_activity: '2026-09-01 10:00:00',
}

async function stubShell(page) {
  await page.route('**/api/pipeline/counts*', (route) =>
    route.fulfill({ json: { counts: COUNTS } }))
  await page.route('**/api/repos', (route) =>
    route.fulfill({ json: { repos: [] } }))
}

/** Records every defect query and answers it under the scope it named. */
async function stubFeatures(page) {
  const seen = []
  await page.route(/\/api\/features(\?.*)?$/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    seen.push(new URL(route.request().url()).searchParams)
    return route.fulfill({
      json: {
        items: [FEATURE],
        pagination: { strategy: 'offset', total: 300, page: 0, size: 50,
                      has_next: true, has_prev: true },
        population: 300,
        extras: { coverage: null, unattributed_defects: 0 },
      },
    })
  })
  return seen
}

const last = (seen) => seen[seen.length - 1] || new URLSearchParams()

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() =>
    localStorage.removeItem('regin.pipeline.project'))
  await stubShell(page)
})

test('test_filter_change_resets_page_and_replaces_history — features',
  async ({ page }) => {
    const seen = await stubFeatures(page)
    await page.goto('/features?page=2')
    await expect.poll(() => last(seen).get('page')).toBe('2')

    await page.getByTestId('feature-stage-building').click()

    // The offset is dropped, not carried: page 2 of the shipped features is
    // not page 2 of the building ones.
    await expect(page).toHaveURL(/stage=building/)
    await expect(page).not.toHaveURL(/page=/)
    await expect.poll(() => last(seen).get('page') ?? '0').toBe('0')

    // And one Back leaves the list rather than undoing the facet: `replace`,
    // never `push`.
    await page.goBack()
    await expect(page).not.toHaveURL(/\/features/)
  })
