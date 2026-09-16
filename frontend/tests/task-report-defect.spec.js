/**
 * The defects a task's page reports — and reads back (regin:79, regin:81).
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
    project_id: null, parent_id: null, assignee: null, assignee_name: null,
    external_id: null, created_by: 1, created_at: '2026-09-14 10:00:00',
    updated_at: '2026-09-14 10:00:00', params: {}, verify_cmd: null,
    autorun: false, rounds: 1, images: [], child_count: 0, run_count: 0,
    claimed_at: '2026-09-14 11:00:00',
    ...over,
  }
}

function defect(over = {}) {
  return {
    id: 41, feature_key: 'regin:7', symptom: 'the gutter folds',
    status: 'open', round: 1, blamed_against_state: null, missing_check: null,
    suspect_paths: [], fix_provider: null, fix_external_key: null,
    opened_at: '2026-09-14 12:00:00', closed_at: null,
    ...over,
  }
}

async function stubDetail(page, row, grain = { children: 0, runs: 0 },
                          featureKey = 'regin:7') {
  await page.route(`**/api/tasks/${row.id}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({ json: { task: row, grain, feature_key: featureKey } })
  })
  for (const pattern of ['**/api/tasks/*/links*', '**/api/tasks/*/comments*',
                         '**/api/projects*']) {
    await page.route(pattern, (route) => route.fulfill({ json: { items: [] } }))
  }
}

// Scoped listing, so the stub answers the page's own `?feature=` read and not
// whatever the ledger-wide default would return.
async function stubList(page, items) {
  await page.route('**/api/defects?feature=*', (route) =>
    route.fulfill({ json: { items, scope: 'feature' } }))
}

test('test_the_defects_card_appears_only_on_a_top_level_task',
  async ({ page }) => {
    await stubList(page, [])
    await stubDetail(page, task({ id: 8, title: 'a run', parent_id: 7 }),
                     { children: 0, runs: 0 }, 'regin:7')
    await page.goto('/tasks/8')
    await expect(page.getByTestId('task-parent-ledger')).toBeVisible()
    await expect(page.getByTestId('task-report-defect')).toHaveCount(0)
    await expect(page.getByTestId('task-defects-empty')).toHaveCount(0)

    await stubDetail(page, task(), { children: 0, runs: 2 })
    await page.goto('/tasks/7')
    await expect(page.getByTestId('task-ship')).toHaveCount(0)
    await expect(page.getByTestId('task-report-defect')).toBeVisible()
  })

test('test_the_card_lists_the_defects_already_blamed_on_the_feature',
  async ({ page }) => {
    await stubList(page, [defect(), defect({ id: 42, symptom: 'the rail clips' })])
    await stubDetail(page, task())

    await page.goto('/tasks/7')
    const list = page.getByTestId('task-defects-list')
    await expect(list).toContainText('the gutter folds')
    await expect(list).toContainText('the rail clips')
    await expect(list).toContainText('#41')
  })

test('test_submitting_posts_the_key_and_the_report_survives_a_reload',
  async ({ page }) => {
    const stored = []
    await page.route('**/api/defects?feature=*', (route) =>
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
    await expect(page.getByTestId('task-defects-empty')).toBeVisible()
    await page.getByTestId('task-report-defect').click()
    await page.getByTestId('task-defect-symptom').fill('the gutter folds')
    await page.getByTestId('task-defect-submit').click()

    await expect(page.getByTestId('task-defect-result')).toContainText('#41')
    await expect(page.getByTestId('task-defect-result')).toContainText('opened')
    expect(posted[0]).toEqual({ symptom: 'the gutter folds', feature_key: 'regin:7' })
    expect(posted[0].session_id).toBeUndefined()
    // The list, not the outcome line, is the record: it is what a reload keeps.
    await expect(page.getByTestId('task-defects-list')).toContainText('the gutter folds')

    await page.reload()
    await expect(page.getByTestId('task-defects-list')).toContainText('the gutter folds')

    await page.getByTestId('task-report-defect').click()
    await page.getByTestId('task-defect-symptom').fill('the gutter folds')
    await page.getByTestId('task-defect-submit').click()
    await expect(page.getByTestId('task-defect-result')).toContainText('already')
  })

test('test_a_linked_task_reports_against_the_link_key_not_the_native_one',
  async ({ page }) => {
    await page.route('**/api/defects?feature=*', (route) =>
      route.fulfill({ json: { items: [], scope: 'feature' } }))
    const posted = []
    await page.route('**/api/defects', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      posted.push(route.request().postDataJSON())
      await route.fulfill({
        status: 201,
        json: { outcome: 'opened', defect: defect({ feature_key: 'CAI-274' }) },
      })
    })
    await stubDetail(page, task(), { children: 0, runs: 0 }, 'CAI-274')

    await page.goto('/tasks/7')
    await page.getByTestId('task-report-defect').click()
    await page.getByTestId('task-defect-symptom').fill('the gutter folds')
    await page.getByTestId('task-defect-submit').click()

    await expect(page.getByTestId('task-defect-result')).toContainText('#41')
    expect(posted[0].feature_key).toBe('CAI-274')
  })
