/**
 * Closing a defect, and the task that writes the check it named.
 *
 * `0` of 63 defects carried a fix task and `0` tasks had `source='defect'`:
 * the CLI verb existed, the row rendered its result, and no browser could
 * ever write one. The offer is the missing half — the only moment anybody is
 * looking at a defect knowing a test still has to be written is the moment
 * they closed it.
 */
import { test, expect } from './auth-fixture.js'

const OPEN = {
  id: 7, feature_id: 81, feature_title: 'The ledger',
  symptom: 'the gutter folds', status: 'open', round: 1,
  blamed_event_kind: 'started', missing_check: null, suspect_paths: [],
  fix_task_id: null, images: [],
  opened_at: '2026-09-14 12:00:00', closed_at: null,
}
const CLOSED = { ...OPEN, status: 'closed', missing_check: 'test_the_gutter_holds' }
const FIXED = { ...CLOSED, fix_task_id: 142 }

function stubList(page, state) {
  return page.route('**/api/defects*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({ json: { items: [state.row], scope: 'all' } })
  })
}

test('closing a defect offers the fix task, and accepting links it',
  async ({ page }) => {
    const state = { row: OPEN }
    const posted = []
    await stubList(page, state)
    await page.route('**/api/defects/*/close', async (route) => {
      posted.push(['close', route.request().postDataJSON()])
      state.row = CLOSED
      await route.fulfill({ json: { defect: CLOSED } })
    })
    await page.route('**/api/defects/*/fix-item', async (route) => {
      posted.push(['fix-item', route.request().postDataJSON()])
      state.row = FIXED
      await route.fulfill({ json: { defect: FIXED } })
    })

    await page.goto('/defects')
    const row = page.locator('li.card', { hasText: 'the gutter folds' })
    await row.getByRole('button', { name: 'Close' }).click()
    await row.getByPlaceholder('the test that would have caught this')
      .fill('test_the_gutter_holds')
    await row.getByTestId('defect-close-submit-7').click()

    // The offer quotes the check back: it is what the task is being filed to
    // write, and an unnamed "create a task?" is a prompt with no argument.
    const offer = row.getByTestId('defect-fix-offer-7')
    await expect(offer).toContainText('test_the_gutter_holds')

    await row.getByTestId('defect-fix-create-7').click()

    expect(posted).toEqual([
      ['close', { missing_check: 'test_the_gutter_holds' }],
      ['fix-item', { create: true }],
    ])
    // The row carries the link now, without a reload: a pointer the operator
    // has to go looking for is one they cannot tell landed.
    await expect(offer).toHaveCount(0)
    await expect(row.locator('dl').getByRole('link', { name: 'task #142' })).toBeVisible()
  })

test('arriving at an already-closed defect raises no offer', async ({ page }) => {
  // The offer answers "you just named a check — who writes it". A defect
  // closed weeks ago is being read, not decided.
  await stubList(page, { row: CLOSED })

  await page.goto('/defects')
  const row = page.locator('li.card', { hasText: 'the gutter folds' })
  await expect(row).toContainText('test_the_gutter_holds')
  await expect(row.getByTestId('defect-fix-offer-7')).toHaveCount(0)
})

test('a refused create says so and leaves the offer standing',
  async ({ page }) => {
    const state = { row: OPEN }
    await stubList(page, state)
    await page.route('**/api/defects/*/close', async (route) => {
      state.row = CLOSED
      await route.fulfill({ json: { defect: CLOSED } })
    })
    await page.route('**/api/defects/*/fix-item', (route) => route.fulfill({
      status: 409,
      json: { error: '#7 is unattributed, so there is no feature to file under' },
    }))

    await page.goto('/defects')
    const row = page.locator('li.card', { hasText: 'the gutter folds' })
    await row.getByRole('button', { name: 'Close' }).click()
    await row.getByPlaceholder('the test that would have caught this')
      .fill('test_the_gutter_holds')
    await row.getByTestId('defect-close-submit-7').click()
    await row.getByTestId('defect-fix-create-7').click()

    await expect(row.getByRole('alert')).toContainText('unattributed')
    await expect(row.getByTestId('defect-fix-offer-7')).toBeVisible()
  })

test('a defect can be pointed at a task that already exists', async ({ page }) => {
  const state = { row: OPEN }
  const posted = []
  await stubList(page, state)
  await page.route('**/api/defects/*/fix-item', async (route) => {
    posted.push(route.request().postDataJSON())
    state.row = { ...OPEN, fix_task_id: 99 }
    await route.fulfill({ json: { defect: state.row } })
  })

  await page.goto('/defects')
  const row = page.locator('li.card', { hasText: 'the gutter folds' })
  await row.getByTestId('defect-more-7').click()
  await page.getByRole('menuitem', { name: 'Link existing task' }).click()
  await row.getByPlaceholder('task id').fill('99')
  await row.getByTestId('defect-fix-item-submit-7').click()

  // A number, not a string: the route reads `task_id` straight off the body
  // and a string there would reach the foreign key as one.
  expect(posted[0]).toEqual({ task_id: 99 })
  await expect(row.locator('dl').getByRole('link', { name: 'task #99' })).toBeVisible()
})

test('an open, attributed defect files its fix task in one click',
  async ({ page }) => {
    const state = { row: OPEN }
    const posted = []
    await stubList(page, state)
    await page.route('**/api/defects/*/fix-item', async (route) => {
      posted.push(route.request().postDataJSON())
      state.row = { ...OPEN, fix_task_id: 143 }
      await route.fulfill({ json: { defect: state.row } })
    })

    await page.goto('/defects')
    const row = page.locator('li.card', { hasText: 'the gutter folds' })
    await row.getByTestId('defect-more-7').click()
    await page.getByTestId('defect-fix-create-row-7').click()

    expect(posted).toEqual([{ create: true }])
    await expect(row.locator('dl').getByRole('link', { name: 'task #143' })).toBeVisible()
    await expect(row.getByTestId('defect-fix-created-7')).toContainText('task #143')
    // A task is pointed at now, so the menu stops offering a second one.
    await row.getByTestId('defect-more-7').click()
    await expect(page.getByRole('menuitem', { name: 'Re-point fix task' })).toBeVisible()
    await expect(page.getByTestId('defect-fix-create-row-7')).toHaveCount(0)
  })

test('an unattributed defect cannot file one, and says why', async ({ page }) => {
  await stubList(page, { row: { ...OPEN, feature_id: null, feature_title: null } })

  await page.goto('/defects')
  const row = page.locator('li.card', { hasText: 'the gutter folds' })
  await row.getByTestId('defect-more-7').click()
  await expect(page.getByTestId('defect-fix-create-row-7'))
    .toHaveAttribute('aria-disabled', 'true')
  await expect(row.getByTestId('defect-fix-create-blocked-7'))
    .toContainText('Attribute it to a feature')
})

test('the row menu is fully visible on the defects ledger, outside a feature row',
  async ({ page }) => {
    await stubList(page, { row: OPEN })
    await page.goto('/defects')
    const more = page.locator('li.card', { hasText: 'the gutter folds' })
      .getByTestId('defect-more-7')
    await page.mouse.move(0, 0)
    expect(await more.evaluate((el) => getComputedStyle(el).opacity)).toBe('1')
  })
