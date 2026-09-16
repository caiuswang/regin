/**
 * Feature #73 · R6 — the Pipeline shell is one page, not three that share a
 * header. Each test here is an acceptance item named in the spec.
 */
import { test, expect } from './auth-fixture.js'

const COUNTS = { tasks: 7, features: 3, defects: 5 }

async function stubCounts(page, body = { counts: COUNTS, project: 'all' }) {
  await page.route('**/api/pipeline/counts*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json',
                    body: JSON.stringify(body) }))
}

test('test_shell_publishes_all_three_counts', async ({ page }) => {
  await stubCounts(page)
  for (const path of ['/tasks', '/features', '/defects']) {
    await page.goto(path)
    await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
    for (const [key, n] of Object.entries(COUNTS)) {
      await expect(page.getByTestId(`pipeline-count-${key}`)).toHaveText(String(n))
    }
  }
})

test('test_shell_publishes_all_three_counts — one request, under the scope',
  async ({ page }) => {
    const asked = []
    await page.route('**/api/pipeline/counts*', (route) => {
      asked.push(new URL(route.request().url()).searchParams.get('project'))
      return route.fulfill({ status: 200, contentType: 'application/json',
                             body: JSON.stringify({ counts: COUNTS }) })
    })
    await page.goto('/tasks')
    await expect(page.getByTestId('pipeline-count-tasks')).toBeVisible()
    expect(asked).toEqual([null])

    await page.goto('/features?project=none')
    await expect(page.getByTestId('pipeline-count-defects')).toBeVisible()
    expect(asked.at(-1)).toBe('none')
  })

test('test_counts_failure_does_not_blank_the_list', async ({ page }) => {
  await page.route('**/api/pipeline/counts*', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json',
                    body: JSON.stringify({ error: 'boom' }) }))
  await page.route('**/api/tasks/counts*', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json',
                    body: JSON.stringify({ error: 'boom' }) }))

  await page.goto('/tasks')
  // The strip renders — without numbers, and saying so.
  await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
  await expect(page.getByTestId('pipeline-count-tasks')).toHaveCount(0)
  await expect(page.getByTestId('pipeline-counts-error')).toBeVisible()
  await expect(page.getByTestId('task-counts-error')).toBeVisible()
  // And the rows still load: the ungrouped feed is what a failed count falls
  // back to, never a blank page.
  await expect(page.getByTestId('task-list')).toBeVisible()
})

test('test_tab_roundtrip_preserves_query_and_scroll', async ({ page }) => {
  await stubCounts(page)
  await page.goto('/tasks?status=idea')
  await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
  // Settle the tab's own content first: a height still arriving would move
  // the offset out from under the assertion.
  await expect(page.getByTestId('tasks-view-tabs')).toBeVisible()

  // Give the page something to scroll, then scroll the app-shell scroller.
  await page.evaluate(() => {
    const el = document.querySelector('.content-scroll')
    el.style.minHeight = '0'
    const spacer = document.createElement('div')
    spacer.id = 'e2e-spacer'
    spacer.style.height = '3000px'
    el.appendChild(spacer)
    el.scrollTop = 900
  })
  const before = await page.evaluate(() =>
    document.querySelector('.content-scroll').scrollTop)
  expect(before).toBeGreaterThan(0)

  // `dispatchEvent`, not `click`: Playwright scrolls a target into view first,
  // and the tab strip is at the top of the very scroller under test — a real
  // click would reset the offset before the navigation it is meant to survive.
  await page.getByTestId('pipeline-tab-features').dispatchEvent('click')
  await expect(page).toHaveURL(/\/features$/)

  await page.getByTestId('pipeline-tab-tasks').click()
  await expect(page).toHaveURL(/status=idea/)
  await expect(page.getByTestId('pipeline-tab-tasks')).toHaveClass(/is-active/)
  // Kept alive, so the spacer the leaving tab was scrolled over is still there.
  await expect(page.locator('#e2e-spacer')).toHaveCount(1)
  await expect.poll(() => page.evaluate(() =>
    document.querySelector('.content-scroll').scrollTop)).toBe(before)
})

test('test_feature_detail_keeps_the_tab_strip', async ({ page }) => {
  await stubCounts(page)
  await page.goto('/features/1')
  await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
  await expect(page.locator('.page-title').first()).toHaveText('Pipeline')
  const features = page.getByTestId('pipeline-tab-features')
  await expect(features).toHaveClass(/is-active/)
  await expect(features).toHaveAttribute('aria-current', 'page')
})

test('the project scope carries from tab to tab', async ({ page }) => {
  await stubCounts(page)
  await page.goto('/tasks?project=none')
  await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
  await page.getByTestId('pipeline-tab-defects').click()
  await expect(page).toHaveURL(/\/defects\?.*project=none/)
  await page.getByTestId('pipeline-tab-features').click()
  await expect(page).toHaveURL(/\/features\?.*project=none/)
})

test('the scope the shell names is the scope the Tasks list sends',
  async ({ page }) => {
    await stubCounts(page)
    const asked = []
    page.on('request', (req) => {
      const url = new URL(req.url())
      if (url.pathname.startsWith('/api/tasks')) {
        asked.push(url.searchParams.get('project_id'))
      }
    })
    await page.goto('/tasks?project=none')
    await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
    await expect.poll(() => asked.filter(v => v === 'none').length)
      .toBeGreaterThan(0)

    // A kept-alive list has no remount to re-seed it, so the scope has to
    // reach it through the URL it is already watching.
    await page.getByTestId('pipeline-tab-features').click()
    await page.getByTestId('pipeline-tab-tasks').click()
    await expect(page).toHaveURL(/project=none/)
    await expect(page.getByTestId('task-filter-project')).toContainText('No project')
  })
