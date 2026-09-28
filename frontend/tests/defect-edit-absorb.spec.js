/**
 * Defect #194: a defect's wording can be corrected after it is filed, and
 * several duplicates fold into the row the operator is looking at in one call.
 */
import { test, expect } from './auth-fixture.js'

const ROW = {
  id: 7, feature_id: 81, feature_title: 'The ledger',
  symptom: 'the gutter folds', status: 'closed', round: 0,
  blamed_event_kind: 'started', missing_check: 'test_gutter', suspect_paths: [],
  fix_task_id: null, images: [],
  opened_at: '2026-09-14 12:00:00', closed_at: '2026-09-15 12:00:00',
}

function stubRow(page, state, calls) {
  return page.route(/\/api\/defects\/\d+$/, async (route) => {
    const req = route.request()
    if (req.method() === 'PATCH') {
      calls.push(['PATCH', req.postDataJSON()])
      state.row = { ...state.row, ...req.postDataJSON() }
      return route.fulfill({ json: { defect: state.row } })
    }
    return route.fulfill({ json: { defect: state.row } })
  })
}

async function openMenu(page, label) {
  await page.getByTestId('defect-more-7').click()
  await page.getByRole('menuitem', { name: label }).click()
}

test('a closed defect can be re-edited, starting from its current wording',
  async ({ page }) => {
    const state = { row: ROW }
    const calls = []
    await stubRow(page, state, calls)
    await page.goto('/defects/7')

    await openMenu(page, 'Edit symptom')
    const field = page.getByTestId('defect-edit-field-7')
    await expect(field).toHaveValue('the gutter folds')
    await field.fill('the gutter folds the newest span')
    await page.getByTestId('defect-edit-submit-7').click()

    expect(calls).toEqual([['PATCH', { symptom: 'the gutter folds the newest span' }]])
    await expect(page.getByTestId('defect-row-7').locator('.symptom'))
      .toHaveText('the gutter folds the newest span')
  })

test('a blank edit cannot be sent', async ({ page }) => {
  await stubRow(page, { row: ROW }, [])
  await page.goto('/defects/7')
  await openMenu(page, 'Edit symptom')
  await page.getByTestId('defect-edit-field-7').fill('   ')
  await expect(page.getByTestId('defect-edit-submit-7')).toBeDisabled()
})

test('absorbing sends every duplicate id in one call, and shows a refusal',
  async ({ page }) => {
    const state = { row: ROW }
    await stubRow(page, state, [])
    const posted = []
    let refuse = true
    await page.route('**/api/defects/7/absorb', (route) => {
      posted.push(route.request().postDataJSON())
      return refuse
        ? route.fulfill({ status: 409, json: {
          error: '#7 is closed and #18 still open — reopen 7 first' } })
        : route.fulfill({ json: { defect: state.row } })
    })
    await page.goto('/defects/7')

    await openMenu(page, 'Absorb duplicates…')
    const field = page.getByPlaceholder('duplicate ids, e.g. 18, 19')
    await field.fill('18, x')
    await expect(page.getByTestId('defect-absorb-submit-7')).toBeDisabled()
    await field.fill('18, 19 19')
    await page.getByTestId('defect-absorb-submit-7').click()
    await expect(page.getByRole('alert')).toContainText('#18 still open')

    refuse = false
    await page.getByTestId('defect-absorb-submit-7').click()
    expect(posted).toEqual([{ duplicates: [18, 19] }, { duplicates: [18, 19] }])
    await expect(page.getByPlaceholder('duplicate ids, e.g. 18, 19')).toHaveCount(0)
  })
