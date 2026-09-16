/**
 * Defect #156 (feature #73) — a detail's back link returns you to the list you
 * opened the row from, not to the default one.
 *
 * The regression it guards: `<router-link to="/tasks">` was a constant path,
 * so every axis the list held in its URL — a search, a feature scope, the
 * project — was dropped on the way back and the reader landed on a list they
 * had not asked for.
 */
import { test, expect } from './auth-fixture.js'

const COUNTS = { tasks: 7, features: 3, defects: 5 }
const ROW = '[data-testid="task-row"]'

function taskRows(n, base = 1000) {
  return Array.from({ length: n }, (_, i) => ({
    id: base + i, title: `row ${i}`, status: 'idea', priority: 'none',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    project_id: null, feature_id: null, run_count: 0, images: [],
  }))
}

// The feed answers the FILTER, so a query silently dropped shows up as a row
// count rather than only as a URL a test could read past.
async function stub(page) {
  await page.route('**/api/users', r => r.fulfill(
    { status: 200, contentType: 'application/json', body: '[]' }))
  await page.route('**/api/pipeline/counts*', r => r.fulfill(
    { status: 200, contentType: 'application/json',
      body: JSON.stringify({ counts: COUNTS }) }))
  await page.route('**/api/tasks/counts*', (route) => {
    const scoped = new URL(route.request().url()).searchParams.get('feature_id')
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        total: scoped ? 2 : 5, by_status: { idea: scoped ? 2 : 5 },
        by_priority: {}, by_project: {},
      }),
    })
  })
  await page.route(/\/api\/tasks\/\d+(\?|$)/, (route) => {
    const id = Number(route.request().url().match(/\/tasks\/(\d+)/)[1])
    return route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({
        id, title: 'row 0', status: 'idea', priority: 'none', body: '',
        created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
        tags: [], project_id: null, feature_id: null, run_count: 0,
        images: [], comments: [], deps: [],
      }) })
  })
  await page.route('**/api/tasks?*', (route) => {
    const scoped = new URL(route.request().url()).searchParams.get('feature_id')
    return route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({
        items: taskRows(scoped ? 2 : 5),
        pagination: { next_cursor: null, size: 50, has_next: false },
      }) })
  })
}

test('test_back_from_a_task_returns_to_the_list_it_was_opened_from',
  async ({ page }) => {
    await stub(page)
    await page.goto('/tasks?feature_id=42')
    await expect(page.locator(ROW)).toHaveCount(2)

    await page.getByTestId('task-id').first().click()
    await expect(page).toHaveURL(/\/tasks\/1000$/)

    await page.locator('.td-back').click()
    await expect(page).toHaveURL(/\/tasks\?feature_id=42$/)
    await expect(page.locator(ROW)).toHaveCount(2)
  })

test('test_back_from_a_deep_linked_task_lands_on_the_bare_list',
  async ({ page }) => {
    await stub(page)
    // No list was left, so there is no query to carry — and inventing one
    // would be a filter the reader never chose.
    await page.goto('/tasks/1000')
    await page.locator('.td-back').click()
    await expect(page).toHaveURL(/\/tasks$/)
    await expect(page.locator(ROW)).toHaveCount(5)
  })
