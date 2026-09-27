/**
 * Feature #73 · R1 + R5 — the Tasks tab's list state is its URL.
 *
 * Each test named `test_*` is an acceptance item the spec names. The rest
 * guard the two things the move to the URL could quietly break: what
 * "Select all N loaded" is allowed to count, and how many requests a mount
 * costs.
 */
import { test, expect } from './auth-fixture.js'

const COUNTS = { tasks: 7, features: 3, defects: 5 }

// Deterministic rows, so a shared `agent_tasks` table cannot decide whether a
// group renders. Every test here is about the query, never about the ledger.
function taskRows(n, status = 'idea') {
  return Array.from({ length: n }, (_, i) => ({
    id: 1000 + i, title: `row ${i}`, status, priority: 'none',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    project_id: null, feature_id: null, run_count: 0, images: [],
  }))
}

async function stubUsers(page, users = [{ id: 2, display_name: 'Ada' }]) {
  await page.route('**/api/users', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json',
                    body: JSON.stringify(users) }))
}

async function stubShell(page) {
  await page.route('**/api/pipeline/counts*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json',
                    body: JSON.stringify({ counts: COUNTS }) }))
}

/** Records every `/api/tasks` query the page issued, and answers them. */
function stubFeed(page, { counts = null, rows = taskRows(3) } = {}) {
  const asked = []
  const body = counts || {
    total: 4, by_status: { idea: 2, done: 2 }, by_priority: {}, by_project: {},
  }
  page.route('**/api/tasks/counts*', (route) => {
    asked.push(new URL(route.request().url()))
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify(body) })
  })
  page.route('**/api/tasks?*', (route) => {
    const url = new URL(route.request().url())
    asked.push(url)
    const status = url.searchParams.get('status')
    const items = status === 'done' ? rows.slice(0, 2) : rows
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        items,
        pagination: { total: items.length, page: 0, size: 50,
                      has_next: false, has_prev: false },
        population: rows.length,
        applied: { sort: url.searchParams.get('sort') || 'created:desc', filters: {} },
        extras: { counts: body },
      }),
    })
  })
  return asked
}

// Every toolbar Select is a Reka combobox portalled to the end of <body>, so
// a choice is a click on the trigger and a click on the option — never
// `selectOption`, which needs a native <select>.
async function choose(page, testId, label) {
  await page.getByTestId(testId).click()
  await page.getByRole('option', { name: label, exact: true }).click()
}

function value(page, testId) {
  return page.getByTestId(testId).locator('.ds-select-value')
}

function countsQueries(asked) {
  return asked.filter(u => u.pathname.endsWith('/tasks/counts'))
}

test.beforeEach(async ({ page }) => {
  // Once per tab, not once per navigation: `regin.tasks.collapsed` is exactly
  // the kind of state a reload is supposed to carry, so an init script that
  // re-cleared it would make the test that checks that pass vacuously.
  await page.addInitScript(() => {
    if (sessionStorage.getItem('e2e-filters-cleared')) return
    sessionStorage.setItem('e2e-filters-cleared', '1')
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith('regin.tasks.') || key === 'regin.pipeline.project') {
        localStorage.removeItem(key)
      }
    }
  })
  await stubShell(page)
})

test('test_task_filters_round_trip_through_url', async ({ page }) => {
  const asked = stubFeed(page)
  await page.goto('/tasks')
  await expect(page.getByTestId('list-facet-status')).toBeVisible()

  await choose(page, 'list-facet-status', 'Done')
  await choose(page, 'list-facet-priority', 'High')
  await choose(page, 'list-sort', 'Updated')
  await choose(page, 'task-group', 'No grouping')

  // Every control is in the URL, so the screen is a link.
  await expect(page).toHaveURL(/status=done/)
  await expect(page).toHaveURL(/priority=high/)
  await expect(page).toHaveURL(/sort=updated(%3A|:)desc/)
  await expect(page).toHaveURL(/group=none/)

  const shared = page.url()
  // …and the server was asked for exactly what the URL says.
  await expect.poll(() => {
    const last = asked.filter(u => u.pathname.endsWith('/api/tasks')).at(-1)
    return last && [last.searchParams.get('status'),
                    last.searchParams.get('priority'),
                    last.searchParams.get('sort')].join('|')
  }).toBe('done|high|updated:desc')

  // Opening that link in a fresh tab shows the same controls and the same rows.
  const fresh = await page.context().newPage()
  stubFeed(fresh)
  await stubShell(fresh)
  await fresh.goto(shared)
  await expect(value(fresh, 'list-facet-status')).toHaveText('Done')
  await expect(value(fresh, 'list-facet-priority')).toHaveText('High')
  await expect(value(fresh, 'list-sort')).toHaveText('Updated')
  await expect(fresh.getByTestId('task-list').getByTestId('task-row'))
    .toHaveCount(2)
  await fresh.close()
})

test('test_bare_path_rewrites_stored_filters_into_url', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('regin.tasks.status', 'done')
    localStorage.setItem('regin.tasks.group', 'none')
  })
  const asked = stubFeed(page)

  await page.goto('/tasks')
  // Restored AND stated: a filtered list under a bare URL is not linkable.
  await expect(page).toHaveURL(/status=done/)
  await expect(value(page, 'list-facet-status')).toHaveText('Done')
  await expect(page.getByTestId('task-list').getByTestId('task-row'))
    .toHaveCount(2)

  // And the restore did not cost a request under the bare query first.
  expect(countsQueries(asked).every(u => u.searchParams.get('status') === 'done'))
    .toBe(true)
})

test('the URL wins over the stored value, and an illegal one is dropped',
  async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('regin.tasks.status', 'done')
      localStorage.setItem('regin.tasks.sort', 'by_vibes')
      localStorage.setItem('regin.tasks.group', 'none')
    })
    stubFeed(page)

    await page.goto('/tasks?status=idea')
    await expect(value(page, 'list-facet-status')).toHaveText('Ideas')
    // An option that no longer exists falls back to the default and stops
    // being offered again.
    await expect(value(page, 'list-sort')).toHaveText('Created')
    await expect(page).not.toHaveURL(/by_vibes/)
    expect(await page.evaluate(
      () => localStorage.getItem('regin.tasks.sort'))).toBeNull()
  })

test('a filter change replaces history rather than stacking it',
  async ({ page }) => {
    stubFeed(page)
    await page.goto('/tasks?group=none')
    await expect(page.getByTestId('list-facet-status')).toBeVisible()

    await choose(page, 'list-facet-status', 'Done')
    await expect(page).toHaveURL(/status=done/)
    await choose(page, 'list-facet-priority', 'High')
    await expect(page).toHaveURL(/priority=high/)

    // One Back leaves the page, not the last two controls.
    await page.goBack()
    await expect(page).not.toHaveURL(/\/tasks/)
  })

test('the four axes the toolbar never offered are reachable and shareable',
  async ({ page }) => {
    const asked = stubFeed(page)
    await page.goto('/tasks?group=none')
    await page.getByTestId('task-more-filters').click()

    await page.getByTestId('task-filter-tag').fill('release')
    await page.getByTestId('task-filter-source').fill('linear')
    await page.getByTestId('task-filter-feature').fill('73')
    await page.getByTestId('task-more-apply').click()

    await expect(page).toHaveURL(/tag=release/)
    await expect(page).toHaveURL(/source=linear/)
    await expect(page).toHaveURL(/feature_id=73/)
    await expect(page.getByTestId('task-more-count')).toHaveText('3')

    await expect.poll(() => {
      const last = asked.filter(u => u.pathname.endsWith('/api/tasks')).at(-1)
      return last && [last.searchParams.get('tag'),
                      last.searchParams.get('source'),
                      last.searchParams.get('feature_id')].join('|')
    }).toBe('release|linear|73')
  })

test('test_counts_failure_does_not_blank_the_list — in grouped mode',
  async ({ page }) => {
    // Grouped is the default, and grouping is built out of the counts. A
    // failure there used to leave no headers, no rows and no error.
    await page.route('**/api/tasks/counts*', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json',
                      body: JSON.stringify({ error: 'boom' }) }))
    await page.route('**/api/tasks?*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json',
                      body: JSON.stringify({
                        items: taskRows(3),
                        pagination: { total: 3, page: 0, size: 50,
                                      has_next: false, has_prev: false },
                        population: 3,
                      }) }))

    await page.goto('/tasks')
    await expect(page.getByTestId('task-counts-error')).toBeVisible()
    await expect(page.getByTestId('task-list')).toBeVisible()
    await expect(page.getByTestId('task-row')).toHaveCount(3)
    await expect(page.getByTestId('list-status')).toHaveCount(0)
  })

test('collapsing a group withdraws its rows from select-all',
  async ({ page }) => {
    stubFeed(page)
    await page.goto('/tasks')
    await expect(page.getByTestId('task-group-idea')).toBeVisible()

    // Both groups open: the union of what is rendered.
    await expect(page.getByTestId('task-select-all'))
      .toContainText('Select all 3 loaded')

    await page.getByTestId('task-group-toggle-done').click()
    await expect(page.getByTestId('task-group-done')
      .getByTestId('task-row')).toHaveCount(0)
    // Still 3, because both groups were serving the same three ids — so
    // collapse the other one too and the offer disappears entirely.
    await page.getByTestId('task-group-toggle-idea').click()
    await expect(page.getByTestId('task-select-all')).toHaveCount(0)
  })

test('a collapsed group is remembered, and costs no request on the next mount',
  async ({ page }) => {
    const asked = stubFeed(page)
    await page.goto('/tasks')
    await page.getByTestId('task-group-toggle-done').click()
    await expect(page.getByTestId('task-group-done')
      .getByTestId('task-row')).toHaveCount(0)

    asked.length = 0
    await page.reload()
    await expect(page.getByTestId('task-group-idea')).toBeVisible()
    // Still collapsed, and nothing was fetched for it.
    await expect(page.getByTestId('task-group-done')
      .getByTestId('task-row')).toHaveCount(0)
    await expect.poll(() => asked.some(
      u => u.pathname.endsWith('/api/tasks')
        && u.searchParams.get('status') === 'idea')).toBe(true)
    expect(asked.some(u => u.pathname.endsWith('/api/tasks')
      && u.searchParams.get('status') === 'done')).toBe(false)
  })

test('the assignee control reaches the URL and the wire', async ({ page }) => {
  await stubUsers(page)
  const asked = stubFeed(page)
  await page.goto('/tasks?group=none')
  await page.getByTestId('task-more-filters').click()

  await choose(page, 'task-filter-assignee', 'Ada')
  await page.getByTestId('task-more-apply').click()

  await expect(page).toHaveURL(/assignee=2/)
  await expect.poll(() => asked.filter(u => u.pathname.endsWith('/api/tasks'))
    .at(-1)?.searchParams.get('assignee')).toBe('2')
})

test('a filter change costs each open group exactly one request', async ({ page }) => {
  const asked = stubFeed(page)
  await page.goto('/tasks')
  await expect(page.getByTestId('task-group-idea')).toBeVisible()
  await expect(page.getByTestId('task-group-done')).toBeVisible()

  asked.length = 0
  await choose(page, 'list-facet-priority', 'High')
  await expect(page).toHaveURL(/priority=high/)

  // Two open groups → two page requests and one counts request. The group
  // used to answer both its own watcher and the list's reload.
  await expect.poll(() => countsQueries(asked).length).toBe(1)
  await expect.poll(
    () => asked.filter(u => u.pathname.endsWith('/api/tasks')).length).toBe(2)
})

test('restoring a client-side axis alone does not re-request the page',
  async ({ page }) => {
    // `group` is rendered in the browser: restoring it rewrites the URL while
    // leaving the server query identical, which must not read as a change.
    await page.addInitScript(() => {
      localStorage.setItem('regin.tasks.group', 'none')
    })
    const asked = stubFeed(page)

    await page.goto('/tasks')
    await expect(page).toHaveURL(/group=none/)
    await expect(page.getByTestId('task-list')).toBeVisible()
    // Ungrouped, the page's own read carries the counts: one request, total.
    await expect.poll(() => asked.length).toBe(1)
    expect(countsQueries(asked)).toHaveLength(0)
  })

test('a bare /tasks reached again from the sidebar still restores its filters',
  async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('regin.tasks.status', 'done')
      localStorage.setItem('regin.tasks.group', 'none')
    })
    stubFeed(page)

    await page.goto('/tasks')
    await expect(page).toHaveURL(/status=done/)

    // Away, then back by a route that is NOT the tab strip's remembering link.
    // The tab is kept alive, so there is no remount to restore from.
    await page.goto('/repos')
    await expect(page).toHaveURL(/\/repos/)
    // The sidebar lands on the Overview; the Tasks tab is one click from it.
    await page.locator('nav.sb-nav .sb-item', { hasText: 'Pipeline' }).click()
    await expect(page).toHaveURL(/\/overview/)
    await page.getByTestId('pipeline-tab-tasks').click()

    await expect(page).toHaveURL(/status=done/)
    await expect(value(page, 'list-facet-status')).toHaveText('Done')
  })

test('an arrival scope suppresses a stored narrowing, and leaving restores it',
  async ({ page }) => {
    // The feature index links `/tasks?feature_id=N`, and the count clicked is
    // the whole of that feature's tasks — so yesterday's `status=done` must
    // not open a shorter list than the number that opened it.
    await page.addInitScript(() => {
      localStorage.setItem('regin.tasks.status', 'done')
      localStorage.setItem('regin.tasks.group', 'none')
    })
    const asked = stubFeed(page)

    await page.goto('/tasks?feature_id=242')
    await expect(page.getByTestId('tasks-scoped-to')).toBeVisible()
    await expect(page).not.toHaveURL(/status=done/)
    await expect.poll(() => asked.length).toBeGreaterThan(0)
    expect(asked.every(u => !u.searchParams.get('status'))).toBe(true)

    // Suppressed, not cleared: the preference is still there to come back to.
    expect(await page.evaluate(
      () => localStorage.getItem('regin.tasks.status'))).toBe('done')
  })
