/**
 * The defect ledger page — its scopes, and the writes it carries.
 *
 * `all` is the default, and that is the whole repair: unattributed used to be,
 * so a defect blamed at the moment it was reported belonged to no scope this
 * page could open. The operator was told it was saved and then could not find
 * it here, on the task, or on /features.
 *
 * Re-attribution is offered because `attribute()` now re-takes the blame
 * snapshot when the key actually changes (defect #137). While it did not, a
 * second attribution left the first feature's state standing.
 */
import { test, expect } from './auth-fixture.js'

function defect(over = {}) {
  return {
    id: 1, feature_key: null, symptom: 'the gutter folds', status: 'open',
    round: 1, blamed_against_state: null, missing_check: null,
    suspect_paths: [], fix_provider: null, fix_external_key: null,
    opened_at: '2026-09-14 12:00:00', closed_at: null,
    ...over,
  }
}

const BLAMED = defect({ id: 2, feature_key: 'regin:81', symptom: 'the rail clips',
                        blamed_against_state: 'started' })
const LOOSE = defect()

// One route for every scope, answering from the query the page sent — so a
// test cannot pass by the page reading a scope it never asked for.
async function stubLedger(page, seen = []) {
  await page.route('**/api/defects*', (route) => {
    const url = new URL(route.request().url())
    seen.push(url.search)
    if (url.searchParams.get('feature')) {
      return route.fulfill({ json: { items: [BLAMED], scope: 'feature' } })
    }
    if (url.searchParams.get('scope') === 'unattributed') {
      return route.fulfill({ json: { items: [LOOSE], scope: 'unattributed' } })
    }
    return route.fulfill({ json: { items: [BLAMED, LOOSE], scope: 'all' } })
  })
}

test('the default scope is every defect, attributed or not',
  async ({ page }) => {
    const seen = []
    await stubLedger(page, seen)

    await page.goto('/defects')
    await expect(page.getByText('2 defects')).toBeVisible()
    await expect(page.getByText('the rail clips')).toBeVisible()
    await expect(page.getByText('the gutter folds')).toBeVisible()
    expect(seen[0]).toBe('')
  })

test('the scope toggle narrows to unattributed and back', async ({ page }) => {
  await stubLedger(page)

  await page.goto('/defects')
  await expect(page.getByTestId('defects-scope-all')).toHaveClass(/is-on/)

  await page.getByTestId('defects-scope-unattributed').click()
  await expect(page.getByText('1 defect', { exact: true })).toBeVisible()
  await expect(page.getByText('the rail clips')).toHaveCount(0)
  await expect(page.getByTestId('defects-scope-unattributed'))
    .toHaveClass(/is-on/)

  await page.getByTestId('defects-scope-all').click()
  await expect(page.getByText('2 defects')).toBeVisible()
})

test('a feature scope says what it is scoped to and offers the way out',
  async ({ page }) => {
    await stubLedger(page)

    await page.goto('/defects?feature=regin%3A81')
    await expect(page.getByTestId('defects-scoped-to')).toContainText('regin:81')
    await expect(page.getByText('the gutter folds')).toHaveCount(0)
    // The toggles would be controls with no key to put in them here.
    await expect(page.getByTestId('defects-scope-all')).toHaveCount(0)

    await page.getByTestId('defects-scoped-to').getByRole('link').click()
    await expect(page.getByText('2 defects')).toBeVisible()
  })

test('an already-blamed defect offers re-attribution', async ({ page }) => {
  // The store re-takes the blame snapshot when the key changes, so the row
  // that comes back on the next read carries the NEW feature's state.
  let moved = false
  await page.route('**/api/defects*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const row = moved
      ? { ...BLAMED, feature_key: 'CAI-274', blamed_against_state: 'completed' }
      : BLAMED
    return route.fulfill({ json: { items: [row], scope: 'all' } })
  })
  const posted = []
  await page.route('**/api/defects/*/attribute', async (route) => {
    posted.push(route.request().postDataJSON())
    moved = true
    await route.fulfill({
      json: { defect: { ...BLAMED, feature_key: 'CAI-274',
                        blamed_against_state: 'completed' } },
    })
  })

  await page.goto('/defects')
  const row = page.locator('li.card', { hasText: 'the rail clips' })
  await row.getByRole('button', { name: 'Re-attribute' }).click()
  await row.getByPlaceholder('feature key').fill('CAI-274')
  await row.getByRole('button', { name: /Re-attribute #2|Attribute #2/ }).click()

  expect(posted[0]).toEqual({ feature_key: 'CAI-274' })
  await expect(row).toContainText('CAI-274')
  await expect(row).toContainText('completed')
})

test('attributing on the unattributed scope drops the row and the count',
  async ({ page }) => {
    // The server's answer changes because the write moved the row out of the
    // predicate. A page that splices the row back in shows an attributed
    // defect under "blamed on no feature", still counted.
    let blamed = false
    await page.route('**/api/defects*', (route) => {
      const url = new URL(route.request().url())
      if (route.request().method() !== 'GET') return route.fallback()
      if (url.searchParams.get('scope') === 'unattributed') {
        return route.fulfill({
          json: { items: blamed ? [] : [LOOSE], scope: 'unattributed' } })
      }
      return route.fulfill({ json: { items: [LOOSE], scope: 'all' } })
    })
    await page.route('**/api/defects/*/attribute', async (route) => {
      blamed = true
      await route.fulfill({
        json: { defect: { ...LOOSE, feature_key: 'CAI-274' } } })
    })

    await page.goto('/defects?scope=unattributed')
    await expect(page.getByText('1 defect', { exact: true })).toBeVisible()

    const row = page.locator('li.card', { hasText: 'the gutter folds' })
    await row.getByRole('button', { name: 'Attribute' }).click()
    await row.getByPlaceholder('feature key').fill('CAI-274')
    await row.getByRole('button', { name: 'Attribute #1' }).click()

    await expect(page.getByText('0 defects')).toBeVisible()
    await expect(page.getByText('the gutter folds')).toHaveCount(0)
    await expect(page.getByText('Nothing is unblamed')).toBeVisible()
  })
