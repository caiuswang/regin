/**
 * The defects a task's page reports — and reads back.
 *
 * The list is the defects THIS task is the fix for (`?fix_item=`), not every
 * defect blamed on its feature: listing the feature's ledger on each step made
 * every sibling task look like the one fixing them. A report still goes to the
 * feature, and since a fresh report fixes nothing yet, the outcome links it —
 * that link is how the page that filed it reads it back.
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

// The task page lists the defects THIS task fixes (`?fix_item=`), never the
// feature's whole ledger: the stub answers per task, and a `feature_id=` read
// answers with every defect so a regression to the feature scope is visible.
async function stubList(page, byTask, featureWide = []) {
  const reads = []
  await page.route('**/api/defects?*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const url = new URL(route.request().url())
    reads.push(url.search)
    const fix = url.searchParams.get('fix_item')
    const items = fix != null ? (byTask[fix] || []) : featureWide
    return route.fulfill({ json: { items, scope: fix != null ? 'fix-item' : 'feature' } })
  })
  return reads
}

test('test_the_defects_card_appears_only_on_a_task_under_a_feature',
  async ({ page }) => {
    // A task under no feature has nothing to blame, and inventing one from
    // its own id is exactly the identity overload this model removed.
    await stubList(page, {})
    await stubDetail(page, task({ id: 8, title: 'unplaced', feature_id: null }),
                     null)
    await page.goto('/tasks/8')
    await expect(page.getByTestId('task-no-feature')).toBeVisible()
    await expect(page.getByTestId('feature-report-defect')).toHaveCount(0)

    await stubDetail(page, task())
    await page.goto('/tasks/7')
    await expect(page.getByTestId('feature-report-defect')).toBeVisible()
  })

test('test_the_card_lists_only_the_defects_this_task_fixes',
  async ({ page }) => {
    const mine = defect({ fix_task_id: 7 })
    const sibling = defect({ id: 42, symptom: 'the rail clips', fix_task_id: 9 })
    const unowned = defect({ id: 43, symptom: 'the badge wraps' })
    const reads = await stubList(page, { 7: [mine], 9: [sibling] },
                                 [mine, sibling, unowned])
    // Task and feature ids differ so the footer link cannot pass on a swap.
    await stubDetail(page, task({ feature_id: 274 }), {
      id: 274, title: 'the feature', origin: 'native', external_ref: null,
      stage: 'building',
    })

    await page.goto('/tasks/7')
    const list = page.getByTestId('feature-defects-list')
    await expect(list).toContainText('the gutter folds')
    await expect(list).toContainText('#41')
    await expect(list).not.toContainText('the rail clips')
    await expect(list).not.toContainText('the badge wraps')
    expect(reads.every((q) => q.includes('fix_item=7') && !q.includes('feature_id')))
      .toBe(true)
    await expect(page.getByTestId('feature-defects-all-link'))
      .toHaveAttribute('href', '/features/274#feature-defects')
  })

test('test_a_sibling_step_under_the_same_feature_lists_none_of_them',
  async ({ page }) => {
    const mine = defect({ fix_task_id: 7 })
    await stubList(page, { 7: [mine] }, [mine])
    await stubDetail(page, task({ id: 12, title: 'another step' }))

    await page.goto('/tasks/12')
    await expect(page.getByTestId('feature-defects-empty'))
      .toContainText('not the fix for any defect')
    await expect(page.getByTestId('feature-defects-list')).toHaveCount(0)
  })

test('test_moving_between_steps_of_one_feature_re_reads_the_list',
  async ({ page }) => {
    const first = defect({ fix_task_id: 7 })
    const second = defect({ id: 42, symptom: 'the rail clips', fix_task_id: 9 })
    await stubList(page, { 7: [first], 9: [second] }, [first, second])
    await stubDetail(page, task())
    await stubDetail(page, task({ id: 9, title: 'the other step' }))

    await page.goto('/tasks/7')
    await expect(page.getByTestId('feature-defects-list')).toContainText('the gutter folds')
    await page.evaluate(() => window.history.pushState({}, '', '/tasks/9'))
    await page.evaluate(() => window.dispatchEvent(new PopStateEvent('popstate')))
    const list = page.getByTestId('feature-defects-list')
    await expect(list).toContainText('the rail clips')
    await expect(list).not.toContainText('the gutter folds')
  })

test('test_submitting_posts_the_feature_and_links_the_new_report',
  async ({ page }) => {
    const stored = []
    await stubList(page, {}, stored)
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
    // The card opens the page's one defect dialog, prefilled — no inline form.
    await expect(page.getByTestId('new-defect-feature-chosen')).toContainText('#7')
    await expect(page.getByTestId('feature-defects').locator('form')).toHaveCount(0)
    await page.getByTestId('new-defect-symptom').fill('the gutter folds')
    await page.getByTestId('new-defect-submit').click()

    const flash = page.getByTestId('flash')
    await expect(flash.filter({ hasText: 'Defect #41 reported' })).toBeVisible()
    expect(posted[0]).toEqual({ symptom: 'the gutter folds', feature_id: 7, suspect_paths: [] })
    expect(posted[0].session_id).toBeUndefined()
    // A fresh report fixes nothing yet, so it is not this task's to list; the
    // flash's link is how the page that filed it reads it back.
    await expect(flash.filter({ hasText: 'Defect #41 reported' }).getByTestId('flash-link'))
      .toHaveAttribute('href', '/defects/41')
    await expect(page.getByTestId('feature-defects-empty')).toBeVisible()

    await page.getByTestId('feature-report-defect').click()
    await page.getByTestId('new-defect-symptom').fill('the gutter folds')
    await page.getByTestId('new-defect-submit').click()
    await expect(flash.filter({ hasText: 'matched an open report' })).toBeVisible()
  })

test('test_a_task_reports_against_the_feature_it_is_placed_under',
  async ({ page }) => {
    await stubList(page, {})
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
    await expect(page.getByTestId('new-defect-feature-chosen')).toContainText('#274')
    await page.getByTestId('new-defect-symptom').fill('the gutter folds')
    await page.getByTestId('new-defect-submit').click()

    await expect(page.getByTestId('flash').filter({ hasText: 'Defect #41 reported' })).toBeVisible()
    expect(posted[0].feature_id).toBe(274)
  })
