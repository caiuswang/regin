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
    id: 1, feature_id: null, feature_title: null,
    symptom: 'the gutter folds', status: 'open',
    round: 1, blamed_against_state: null, missing_check: null,
    suspect_paths: [], fix_provider: null, fix_external_key: null,
    opened_at: '2026-09-14 12:00:00', closed_at: null,
    ...over,
  }
}

const BLAMED = defect({ id: 2, feature_id: 81, feature_title: 'The ledger',
  symptom: 'the rail clips',
                        blamed_against_state: 'started' })
const LOOSE = defect()

// One route for every scope, answering from the query the page sent — so a
// test cannot pass by the page reading a scope it never asked for.
async function stubLedger(page, seen = []) {
  await page.route('**/api/defects*', (route) => {
    const url = new URL(route.request().url())
    seen.push(url.search)
    if (url.searchParams.get('feature_id')) {
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
    // Since #73 the tab count is the shell's population under the project
    // scope, from its own endpoint — not this list's length. The scope is
    // therefore asserted on the rows it actually put on screen.
    await expect(page.getByText('the rail clips')).toBeVisible()
    await expect(page.getByText('the gutter folds')).toBeVisible()
    // `all` is the default, so it rides in no parameter. The sort and the
    // page do, because they are facts about the rows on screen.
    expect(new URLSearchParams(seen[0]).get('scope')).toBeNull()
  })

test('the scope toggle narrows to unattributed and back', async ({ page }) => {
  await stubLedger(page)

  await page.goto('/defects')
  await expect(page.getByRole('tab', { name: 'All' }))
    .toHaveAttribute('aria-selected', 'true')

  await page.getByRole('tab', { name: 'Unattributed' }).click()
  await expect(page.getByText('1 defect blamed on no feature')).toBeVisible()
  await expect(page.getByText('the rail clips')).toHaveCount(0)
  await expect(page).toHaveURL(/scope=unattributed/)
  await expect(page.getByRole('tab', { name: 'Unattributed' }))
    .toHaveAttribute('aria-selected', 'true')

  await page.getByRole('tab', { name: 'All' }).click()
  await expect(page.getByText('the rail clips')).toBeVisible()
  await expect(page.getByText('the gutter folds')).toBeVisible()
})

test('a feature scope says what it is scoped to and offers the way out',
  async ({ page }) => {
    await stubLedger(page)

    await page.goto('/defects?feature_id=81')
    await expect(page.getByTestId('defects-scoped-to')).toContainText('The ledger')
    await expect(page.getByText('the gutter folds')).toHaveCount(0)
    // The toggles would be controls with no key to put in them here.
    await expect(page.getByRole('tab', { name: 'All' })).toHaveCount(0)

    await page.getByTestId('defects-scoped-to')
      .getByRole('link', { name: 'every defect' }).click()
    await expect(page.getByText('the gutter folds')).toBeVisible()
  })

test('an already-blamed defect offers re-attribution', async ({ page }) => {
  // The store re-points the blame when the feature changes, so the row that
  // comes back on the next read names the NEW feature's last event.
  let moved = false
  await page.route('**/api/defects*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const row = moved
      ? { ...BLAMED, feature_id: 274, feature_title: 'Another', blamed_event_kind: 'shipped' }
      : BLAMED
    return route.fulfill({ json: { items: [row], scope: 'all' } })
  })
  const posted = []
  await page.route('**/api/defects/*/attribute', async (route) => {
    posted.push(route.request().postDataJSON())
    moved = true
    await route.fulfill({
      json: { defect: { ...BLAMED, feature_id: 274, feature_title: 'Another',
                        blamed_against_state: 'completed' } },
    })
  })

  await page.goto('/defects')
  const row = page.locator('li.card', { hasText: 'the rail clips' })
  await row.getByRole('button', { name: 'Re-attribute' }).click()
  await row.getByPlaceholder('feature id').fill('274')
  await row.getByRole('button', { name: /Re-attribute #2|Attribute #2/ }).click()

  expect(posted[0]).toEqual({ feature_id: 274 })
  await expect(row).toContainText('Another')
  await expect(row).toContainText('shipped')
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
        json: { defect: { ...LOOSE, feature_id: 274, feature_title: 'Another' } } })
    })

    await page.goto('/defects?scope=unattributed')
    await expect(page.getByText('1 defect blamed on no feature')).toBeVisible()

    const row = page.locator('li.card', { hasText: 'the gutter folds' })
    await row.getByRole('button', { name: 'Attribute' }).click()
    await row.getByPlaceholder('feature id').fill('274')
    await row.getByRole('button', { name: 'Attribute #1' }).click()

    await expect(page.getByText('the gutter folds')).toHaveCount(0)
    await expect(page.getByText('Nothing is unblamed')).toBeVisible()
  })

test('a row links to its own page, and the tab strip survives the trip',
  async ({ page }) => {
    await stubLedger(page)
    await page.route('**/api/defects/2', (route) =>
      route.fulfill({ json: { defect: BLAMED } }))

    await page.goto('/defects')
    await page.getByTestId('defect-permalink-2').click()

    await expect(page).toHaveURL(/\/defects\/2$/)
    await expect(page.getByTestId('defect-detail')).toContainText('the rail clips')
    // The permalink is a child of the Pipeline shell, so following it does
    // not take the reader out of the page that framed the row.
    await expect(page.getByTestId('pipeline-tab-defects')).toBeVisible()
  })

test('reopening asks first, and declining writes nothing', async ({ page }) => {
  const CLOSED = defect({ id: 3, status: 'closed', round: 2,
                          symptom: 'the badge lies',
                          missing_check: 'test_badge_matches_state' })
  await page.route('**/api/defects*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({ json: { items: [CLOSED], scope: 'all' } })
  })
  const posted = []
  await page.route('**/api/defects/*/reopen', async (route) => {
    posted.push(route.request().url())
    await route.fulfill({ json: { defect: { ...CLOSED, status: 'open', round: 3 } } })
  })

  await page.goto('/defects')
  const row = page.locator('li.card', { hasText: 'the badge lies' })

  await row.getByRole('button', { name: 'Reopen' }).click()
  // The round it bumps is a figure the feature keeps for good, so it is asked
  // for out loud — and cancelling has to leave the ledger untouched.
  await expect(page.getByText('round 3')).toBeVisible()
  await page.getByRole('button', { name: 'Cancel' }).click()
  expect(posted).toEqual([])

  await row.getByRole('button', { name: 'Reopen' }).click()
  await page.getByRole('button', { name: 'Reopen', exact: true }).last().click()
  await expect.poll(() => posted.length).toBe(1)
})

test('closing without a check is refused before anything is posted',
  async ({ page }) => {
    await stubLedger(page)
    const posted = []
    await page.route('**/api/defects/*/close', async (route) => {
      posted.push(route.request().url())
      await route.fulfill({ json: { defect: LOOSE } })
    })

    await page.goto('/defects')
    const row = page.locator('li.card', { hasText: 'the gutter folds' })
    await row.getByRole('button', { name: 'Close' }).click()

    // Nothing to send, so nothing to press: the rule is stated where the
    // operator can still act on it rather than by a 400 that costs them
    // their place in the list.
    await expect(row.getByTestId('defect-close-submit-1')).toBeDisabled()
    await row.getByPlaceholder('the test that would have caught this').fill('   ')
    await expect(row.getByTestId('defect-close-problem-1'))
      .toContainText('Name the check')
    await expect(row.getByTestId('defect-close-submit-1')).toBeDisabled()
    expect(posted).toEqual([])
  })
