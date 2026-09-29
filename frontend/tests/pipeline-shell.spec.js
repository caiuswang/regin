/**
 * Tasks, features and defects behind one sidebar entry (feature #70).
 *
 * The three were three rail items, which is how the loop they form — queued,
 * claimed done, came back — read as three unrelated tables. The switcher is
 * now a tab strip above the three, mirroring /trace.
 *
 * Asserted against the rendered rail and the real navigations: the failure
 * these cover is an entry that no longer highlights, or a URL that moved.
 */
import { test, expect } from './auth-fixture.js'

const TABS = [
  { testid: 'pipeline-tab-overview', path: '/overview' },
  { testid: 'pipeline-tab-tasks', path: '/tasks' },
  { testid: 'pipeline-tab-features', path: '/features' },
]

test('one rail entry, not three', async ({ page }) => {
  await page.goto('/tasks')
  await page.waitForSelector('.app-shell')
  const labels = await page.$$eval('nav.sb-nav .sb-item', (items) =>
    items.map((i) => (i.getAttribute('aria-label') || i.innerText || '').trim()))
  expect(labels).toContain('Pipeline')
  for (const gone of ['Tasks', 'Features', 'Defects', 'Projects']) {
    expect(labels).not.toContain(gone)
  }
})

test('the tab strip switches between the three, and the URLs do not move',
  async ({ page }) => {
    await page.goto('/tasks')
    await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
    for (const tab of TABS) {
      await page.getByTestId(tab.testid).click()
      await expect(page).toHaveURL(new RegExp(`${tab.path}$`))
      await expect(page.getByTestId(tab.testid)).toHaveClass(/is-active/)
    }
  })

test('a deep link to any of the three lands on its own tab', async ({ page }) => {
  for (const tab of TABS) {
    await page.goto(tab.path)
    await expect(page.getByTestId(tab.testid)).toHaveClass(/is-active/)
    await expect(page.locator('.page-title')).toHaveText('Pipeline')
  }
})

test('the Pipeline entry stays lit on every one of the three, and on a detail page',
  async ({ page }) => {
    const rail = page.locator('nav.sb-nav .sb-item', { hasText: 'Pipeline' })
    for (const path of ['/overview', '/tasks', '/features', '/tasks/1', '/features/1', '/defects/1']) {
      await page.goto(path)
      await page.waitForSelector('.app-shell')
      await expect(rail).toHaveClass(/is-active/)
    }
  })

test("each tab's own controls ride up into the shell header", async ({ page }) => {
  await page.goto('/tasks')
  await expect(page.locator('#pipeline-actions [data-testid="task-new"]')).toBeVisible()
  await page.getByTestId('pipeline-tab-features').click()
  await expect(page.locator('#pipeline-actions [data-testid="declare-feature"]')).toBeVisible()
  // One tab's action must not outlive it.
  await expect(page.locator('#pipeline-actions [data-testid="task-new"]')).toHaveCount(0)
  await page.getByTestId('pipeline-tab-overview').click()
  await expect(page.locator('#pipeline-actions [data-testid="declare-feature"]')).toHaveCount(0)
  await expect(page.locator('#pipeline-actions [data-testid="task-new"]')).toHaveCount(1)
})

test('the shell renders no console error on a cold load of any tab',
  async ({ page }) => {
    const errors = []
    page.on('console', (m) => {
      if (m.type() === 'error' || /Vue warn/.test(m.text())) errors.push(m.text())
    })
    for (const tab of TABS) {
      await page.goto(tab.path)
      await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
    }
    expect(errors.join('\n---\n')).toBe('')
  })

// Feature #125 S2 + S3: the defect pages head the shell like the tabs do, and
// offer New ▾ with Defect as the default.
test('test_defect_pages_publish_subline_and_create', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const row = {
    id: 12, feature_id: 81, feature_title: 'The ledger', symptom: 'the rail clips',
    status: 'open', round: 0, blamed_event_kind: 'started', missing_check: null,
    suspect_paths: [], fix_task_id: null, fix_state: null, triage_state: null,
    triage_reason: null, queue_position: null, images: [],
    opened_at: '2026-09-14 12:00:00', updated_at: '2026-09-14 12:00:00', closed_at: null,
  }
  await page.route(/\/api\/defects(\?.*)?$/, (route) => route.fulfill({ json: {
    items: [row], population: 1,
    pagination: { total: 1, page: 0, size: 50, has_next: false, has_prev: false } } }))
  await page.route(/\/api\/defects\/12$/, (route) => route.fulfill({ json: { defect: row } }))

  await page.goto('/defects')
  await expect(page.getByTestId('pipeline-subline'))
    .toHaveText('Every defect, open or closed, triaged or not.')
  const create = page.locator('#pipeline-actions [data-testid="defect-new"]')
  await expect(create).toContainText('New defect')
  await create.click()
  await expect(page.getByTestId('new-defect-symptom')).toBeVisible()
  // The list names no feature, so nothing is preselected.
  await expect(page.getByTestId('new-defect-feature-chosen')).toHaveCount(0)
  await page.keyboard.press('Escape')

  // In-app, so the shell and its dialog stay mounted across both pages.
  await page.getByTestId('defect-permalink-12').click()
  await expect(page.getByTestId('pipeline-subline')).toHaveText('Defect #12, on its own address.')
  await page.locator('#pipeline-actions [data-testid="defect-new"]').click()
  await expect(page.getByTestId('new-defect-feature-chosen')).toContainText('#81 The ledger')
  await page.keyboard.press('Escape')

  // The ▾ still offers every kind, and `c` opens the page's default.
  await page.getByTestId('pipeline-new').click()
  await expect(page.getByTestId('pipeline-new-task')).toBeVisible()
  await expect(page.getByTestId('pipeline-new-defect')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.locator('body').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('c')
  await expect(page.getByTestId('new-defect-feature-chosen')).toContainText('#81 The ledger')
  await page.keyboard.press('Escape')

  // The prefill belongs to the page that made it: reopened from the list, the
  // report names no feature, on screen or in the request.
  const posted = []
  await page.route(/\/api\/defects$/, (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    posted.push(route.request().postDataJSON())
    return route.fulfill({ status: 201, json: { outcome: 'opened', defect: { ...row, id: 13, feature_id: null } } })
  })
  await page.goBack()
  await expect(page).toHaveURL(/\/defects$/)
  await page.locator('#pipeline-actions [data-testid="defect-new"]').click()
  await expect(page.getByTestId('new-defect-feature-chosen')).toHaveCount(0)
  await page.getByTestId('new-defect-symptom').fill('another symptom')
  await page.getByTestId('new-defect-submit').click()
  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0].feature_id).toBeUndefined()
})
