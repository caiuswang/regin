/**
 * The defects a task's page reports — and reads back.
 *
 * Reporting used to be write-only here: the outcome was a local ref, so a
 * reload left the operator with a defect they had been told was saved and
 * could not find on the page, on /defects, or anywhere else. The list is what
 * makes the report a record rather than a claim.
 *
 * The control lives only on a top-level task: a child is a run, not a feature.
 * The key comes from the detail route, never composed from the id — a task
 * linked to another tracker is a run of that issue and its defects are the
 * issue's.
 */
import { test, expect } from './auth-fixture.js'

function task(over = {}) {
  return {
    id: 7, title: 'the feature', body: '', status: 'done',
    source: 'web', tags: [], links: [], priority: 'none', priority_rank: 4,
    project_id: null, feature_id: 7, assignee: null, assignee_name: null,
    external_id: null, created_by: 1, created_at: '2026-09-14 10:00:00',
    updated_at: '2026-09-14 10:00:00', images: [], run_count: 0,
    ...over,
  }
}

function defect(over = {}) {
  return {
    id: 41, feature_id: 7, feature_title: 'the feature',
    symptom: 'the gutter folds',
    status: 'open', round: 1, blamed_event_kind: null, missing_check: null,
    suspect_paths: [], fix_task_id: null,
    opened_at: '2026-09-14 12:00:00', closed_at: null,
    ...over,
  }
}

async function stubDetail(page, row, feature = {
  id: 7, title: 'the feature', origin: 'native', external_ref: null,
  stage: 'building',
}) {
  await page.route(`**/api/tasks/${row.id}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({ json: { task: row, feature, armed: null } })
  })
  for (const pattern of ['**/api/tasks/*/comments*', '**/api/projects*']) {
    await page.route(pattern, (route) => route.fulfill({ json: { items: [] } }))
  }
}

// Scoped listing, so the stub answers the page's own `?feature_id=` read and
// not whatever the ledger-wide default would return.
async function stubList(page, items) {
  await page.route('**/api/defects?feature_id=*', (route) =>
    route.fulfill({ json: { items, scope: 'feature' } }))
}

test('test_the_defects_card_appears_only_on_a_task_under_a_feature',
  async ({ page }) => {
    // A task under no feature has nothing to blame, and inventing one from
    // its own id is exactly the identity overload this model removed.
    await stubList(page, [])
    await stubDetail(page, task({ id: 8, title: 'unplaced', feature_id: null }),
                     null)
    await page.goto('/tasks/8')
    await expect(page.getByTestId('task-no-feature')).toBeVisible()
    await expect(page.getByTestId('feature-report-defect')).toHaveCount(0)

    await stubDetail(page, task())
    await page.goto('/tasks/7')
    await expect(page.getByTestId('feature-report-defect')).toBeVisible()
  })

test('test_the_card_lists_the_defects_already_blamed_on_the_feature',
  async ({ page }) => {
    await stubList(page, [defect(), defect({ id: 42, symptom: 'the rail clips' })])
    await stubDetail(page, task())

    await page.goto('/tasks/7')
    const list = page.getByTestId('feature-defects-list')
    await expect(list).toContainText('the gutter folds')
    await expect(list).toContainText('the rail clips')
    await expect(list).toContainText('#41')
  })

test('test_submitting_posts_the_key_and_the_report_survives_a_reload',
  async ({ page }) => {
    const stored = []
    await page.route('**/api/defects?feature_id=*', (route) =>
      route.fulfill({ json: { items: stored.slice(), scope: 'feature' } }))
    const posted = []
    await page.route('**/api/defects', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      posted.push(route.request().postDataJSON())
      const row = defect({ symptom: posted[posted.length - 1].symptom })
      if (posted.length === 1) stored.push(row)
      await route.fulfill({
        status: posted.length === 1 ? 201 : 200,
        json: { outcome: posted.length === 1 ? 'opened' : 'deduped',
                defect: row },
      })
    })
    await stubDetail(page, task())

    await page.goto('/tasks/7')
    await expect(page.getByTestId('feature-defects-empty')).toBeVisible()
    await page.getByTestId('feature-report-defect').click()
    await page.getByTestId('feature-defect-symptom').fill('the gutter folds')
    await page.getByTestId('feature-defect-submit').click()

    await expect(page.getByTestId('feature-defect-result')).toContainText('#41')
    await expect(page.getByTestId('feature-defect-result')).toContainText('opened')
    expect(posted[0]).toEqual({ symptom: 'the gutter folds', feature_id: 7 })
    expect(posted[0].session_id).toBeUndefined()
    // The list, not the outcome line, is the record: it is what a reload keeps.
    await expect(page.getByTestId('feature-defects-list')).toContainText('the gutter folds')

    await page.reload()
    await expect(page.getByTestId('feature-defects-list')).toContainText('the gutter folds')

    await page.getByTestId('feature-report-defect').click()
    await page.getByTestId('feature-defect-symptom').fill('the gutter folds')
    await page.getByTestId('feature-defect-submit').click()
    await expect(page.getByTestId('feature-defect-result')).toContainText('already')
  })

test('test_a_task_reports_against_the_feature_it_is_placed_under',
  async ({ page }) => {
    await page.route('**/api/defects?feature_id=*', (route) =>
      route.fulfill({ json: { items: [], scope: 'feature' } }))
    const posted = []
    await page.route('**/api/defects', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      posted.push(route.request().postDataJSON())
      await route.fulfill({
        status: 201,
        json: { outcome: 'opened', defect: defect({ feature_id: 274 }) },
      })
    })
    await stubDetail(page, task({ feature_id: 274 }), {
      id: 274, title: 'another feature', origin: 'linear',
      external_ref: 'CAI-274', stage: 'building',
    })

    await page.goto('/tasks/7')
    await page.getByTestId('feature-report-defect').click()
    await page.getByTestId('feature-defect-symptom').fill('the gutter folds')
    await page.getByTestId('feature-defect-submit').click()

    await expect(page.getByTestId('feature-defect-result')).toContainText('#41')
    expect(posted[0].feature_id).toBe(274)
  })
