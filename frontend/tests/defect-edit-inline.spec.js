/**
 * Defect #194 / D317: a defect's wording is corrected by clicking it — no menu
 * verb, no separate form.
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

test('clicking the symptom edits it, starting from its current wording',
  async ({ page }) => {
    const state = { row: ROW }
    const calls = []
    await stubRow(page, state, calls)
    await page.goto('/defects/7')

    await page.getByTestId('defect-symptom-7').click()
    const field = page.getByTestId('defect-edit-field-7')
    await expect(field).toHaveValue('the gutter folds')
    await expect(field).toBeFocused()
    await field.fill('the gutter folds the newest span')
    await page.getByTestId('defect-edit-submit-7').click()

    expect(calls).toEqual([['PATCH', { symptom: 'the gutter folds the newest span' }]])
    await expect(page.getByTestId('defect-row-7').locator('.symptom'))
      .toHaveText('the gutter folds the newest span')
  })

test('Escape cancels the edit and sends nothing', async ({ page }) => {
  const calls = []
  await stubRow(page, { row: ROW }, calls)
  await page.goto('/defects/7')
  await page.getByTestId('defect-symptom-7').click()
  const field = page.getByTestId('defect-edit-field-7')
  await field.fill('changed my mind')
  await field.press('Escape')
  await expect(field).toHaveCount(0)
  await expect(page.getByTestId('defect-symptom-7')).toHaveText('the gutter folds')
  expect(calls).toEqual([])
})

test('a blank edit cannot be sent', async ({ page }) => {
  await stubRow(page, { row: ROW }, [])
  await page.goto('/defects/7')
  await page.getByTestId('defect-symptom-7').click()
  await page.getByTestId('defect-edit-field-7').fill('   ')
  await expect(page.getByTestId('defect-edit-submit-7')).toBeDisabled()
})
