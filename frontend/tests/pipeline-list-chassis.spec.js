/**
 * Feature #108 — one list chassis for the four Pipeline tabs.
 *
 * Each test is an acceptance scenario from the feature's spec. The count
 * agreement test reads the real e2e server, because I1 is a claim about two
 * endpoints agreeing; the rest stub the list they are about, because they are
 * claims about the query the page sends and what it renders from the answer.
 */
import { test, expect } from './auth-fixture.js'

const TABS = ['/tasks', '/features', '/defects', '/projects']

function envelope(items, over = {}) {
  return {
    items,
    pagination: { strategy: 'offset', total: items.length, page: 0, size: 50,
                  has_next: false, has_prev: false, ...(over.pagination || {}) },
    population: over.population ?? items.length,
    applied: over.applied || { sort: 'created:desc', filters: {} },
    extras: over.extras || {},
  }
}

function taskRow(i) {
  return {
    id: 5000 + i, title: `row ${i}`, status: 'idea', priority: 'none',
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    project_id: null, feature_id: null, run_count: 0, images: [],
  }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (sessionStorage.getItem('e2e-108-cleared')) return
    sessionStorage.setItem('e2e-108-cleared', '1')
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith('regin.tasks.') || key === 'regin.pipeline.project') {
        localStorage.removeItem(key)
      }
    }
  })
})

test('one toolbar, same place, on every tab', async ({ page }) => {
  for (const path of TABS) {
    await page.goto(path)
    const toolbar = page.getByTestId('pipeline-list-toolbar')
    await expect(toolbar).toBeVisible()
    const strip = await page.getByTestId('pipeline-tabs').boundingBox()
    const box = await toolbar.boundingBox()
    expect(box.y, path).toBeGreaterThan(strip.y)
    await expect(page.locator('#pipeline-actions input')).toHaveCount(0)
  }
})

test('tasks has one project control, and it is the shell\'s', async ({ page }) => {
  await page.route('**/api/repos', (route) =>
    route.fulfill({ json: { repos: [{ id: 7, name: 'regin' }] } }))
  const asked = []
  page.on('request', (req) => {
    const url = new URL(req.url())
    if (url.pathname === '/api/tasks') asked.push(url.searchParams.get('project_id'))
  })
  await page.goto('/tasks?group=none')
  await expect(page.getByTestId('pipeline-list-toolbar')).toBeVisible()
  await expect(page.getByTestId('task-filter-project')).toHaveCount(0)

  await page.getByTestId('pipeline-project').click()
  await page.getByRole('option', { name: 'regin', exact: true }).click()
  await expect.poll(() => asked.at(-1)).toBe('7')
})

test('search: Escape reverts without a request, Enter commits by replace',
  async ({ page }) => {
    const asked = []
    await page.route(/\/api\/features(\?.*)?$/, (route) => {
      if (route.request().method() !== 'GET') return route.fallback()
      asked.push(new URL(route.request().url()).searchParams.get('q'))
      return route.fulfill({ json: envelope([], { population: 3 }) })
    })
    await page.goto('/features?q=foo&page=2')
    const box = page.getByTestId('list-search')
    await expect(box).toHaveValue('foo')
    const before = asked.length
    const history = await page.evaluate(() => history.length)

    await box.fill('bar')
    await box.press('Escape')
    await expect(box).toHaveValue('foo')
    expect(asked.length).toBe(before)

    await box.fill('bar')
    await box.press('Enter')
    await expect(page).toHaveURL(/q=bar/)
    await expect(page).not.toHaveURL(/page=/)
    expect(await page.evaluate(() => history.length)).toBe(history)
  })

test('sort is key:dir, and a retired form renders the server default',
  async ({ page }) => {
    const sorts = []
    await page.route('**/api/tasks?*', (route) => {
      const url = new URL(route.request().url())
      sorts.push(url.searchParams.get('sort'))
      return route.fulfill({ json: envelope([taskRow(0)], {
        applied: { sort: 'created:desc', filters: {} } }) })
    })
    await page.goto('/tasks?group=none&sort=created_desc')
    await expect(page.getByTestId('list-sort')).toContainText('Created')
    await expect(page.getByTestId('list-sort-dir'))
      .toHaveAttribute('aria-label', /Sorted descending/)

    await page.getByTestId('list-sort-dir').click()
    await expect(page).toHaveURL(/sort=created(%3A|:)asc/)
    await expect.poll(() => sorts.at(-1)).toBe('created:asc')
  })

for (const key of ['tasks', 'features', 'defects']) {
  for (const scope of ['all', 'none']) {
    test(`the toolbar count agrees with the tab strip — ${key}, ${scope}`,
      async ({ page }) => {
        const query = scope === 'all' ? '' : `?project=${scope}`
        const sep = query ? '&' : '?'
        // Ungrouped, so the tasks count is the flat read's own.
        await page.goto(`/${key}${query}${key === 'tasks' ? `${sep}group=none` : ''}`)
        const strip = page.getByTestId(`pipeline-count-${key}`)
        await expect(strip).toBeVisible()
        const n = (await strip.innerText()).trim()
        await expect(page.getByTestId('list-count'))
          .toHaveText(new RegExp(`^${n} ${key.slice(0, -1)}s?$`))
      })
  }
}

test('Clear N is always present, and counts what narrows', async ({ page }) => {
  await page.goto('/defects')
  await expect(page.getByTestId('list-clear')).toBeVisible()
  await expect(page.getByTestId('list-clear')).toBeDisabled()

  await page.goto('/defects?status=open&q=zz')
  await expect(page.getByTestId('list-clear')).toHaveText(/Clear 2/)
  await expect(page.getByTestId('list-clear')).toBeEnabled()
})

test('the empty states say which kind of nothing they are', async ({ page }) => {
  await page.route(/\/api\/features(\?.*)?$/, (route) =>
    route.fulfill({ json: envelope([], { population: 4 }) }))
  await page.goto('/features?q=nothing-matches')
  const status = page.getByTestId('list-status')
  await expect(status).toHaveAttribute('data-state', 'no-match')
  await expect(status.getByTestId('list-status-clear')).toBeVisible()

  await page.route('**/api/defects*', (route) =>
    route.fulfill({ json: envelope([], {
      population: 3, pagination: { total: 3, page: 99 },
      applied: { sort: 'opened:desc', filters: { scope: 'all' } } }) }))
  await page.goto('/defects?page=99')
  await expect(status).toHaveAttribute('data-state', 'past-end')
  await status.getByTestId('list-status-first').click()
  await expect(page).not.toHaveURL(/page=/)
})

test('the task view is in the URL and survives a reload', async ({ page }) => {
  await page.goto('/tasks')
  await page.getByTestId('list-views').getByRole('tab', { name: 'Graph' }).click()
  await expect(page).toHaveURL(/view=graph/)
  await page.reload()
  await expect(page.getByTestId('list-views').getByRole('tab', { name: 'Graph' }))
    .toHaveAttribute('aria-selected', 'true')
})

test('tasks paginate by offset', async ({ page }) => {
  const pages = []
  await page.route('**/api/tasks?*', (route) => {
    const url = new URL(route.request().url())
    const index = Number(url.searchParams.get('page') || 0)
    pages.push(index)
    const rows = Array.from({ length: 50 }, (_, i) => taskRow(index * 50 + i))
    return route.fulfill({ json: envelope(rows, {
      population: 120,
      pagination: { total: 120, page: index, has_next: index < 2, has_prev: index > 0 },
    }) })
  })
  await page.goto('/tasks?group=none&page=1')
  await expect(page.getByText('51–100')).toBeVisible()
  expect(pages).toContain(1)
  await expect(page.getByTestId('task-row').first()).toContainText('row 50')
})

test('projects narrow on the server', async ({ page }) => {
  const asked = []
  await page.route('**/api/pipeline/projects?*', (route) => {
    asked.push(new URL(route.request().url()).searchParams.get('q'))
    return route.fulfill({ json: envelope([{
      id: 1, scope: '1', name: 'regin', description: '', path: '/r',
      registered: true, status: 'in_progress', progress: { done: 1, live: 2 },
      features: { total: 2, by_stage: {} }, tasks: { total: 0, by_status: {} },
      defects: { total: 0, open: 0 }, last_activity: null,
    }], { population: 4, applied: { sort: 'activity:desc', filters: { q: 'reg' } } }) })
  })
  await page.goto('/projects?q=reg')
  await expect.poll(() => asked.at(-1)).toBe('reg')
  await expect(page.getByTestId('project-row-1')).toBeVisible()
  await expect(page.getByTestId('list-count')).toHaveText('1 of 4 projects')
})

test('/ focuses search on every tab', async ({ page }) => {
  for (const path of TABS) {
    await page.goto(path)
    await expect(page.getByTestId('list-search')).toBeVisible()
    await page.locator('body').click({ position: { x: 5, y: 5 } })
    await page.keyboard.press('/')
    await expect(page.getByTestId('list-search'), path).toBeFocused()
  }
})

test('Clear keeps the address a reader arrived on', async ({ page }) => {
  await page.goto('/tasks?group=none&feature_id=5&status=done')
  await expect(page.getByTestId('list-clear')).toHaveText(/Clear 1/)
  await page.getByTestId('list-clear').click()
  await expect(page).toHaveURL(/feature_id=5/)
  await expect(page).not.toHaveURL(/status=done/)
})

test('a kept-alive Tasks list stays quiet while another tab is on screen',
  async ({ page }) => {
    await page.goto('/tasks?group=none')
    await expect(page.getByTestId('pipeline-list-toolbar')).toBeVisible()
    await page.getByTestId('pipeline-tab-defects').click()
    await expect(page).toHaveURL(/\/defects/)
    const stray = []
    page.on('request', (req) => {
      if (new URL(req.url()).pathname.startsWith('/api/tasks')) stray.push(req.url())
    })
    // Both lists watch the same route change, so by the time the defects read
    // for it has answered, any read the hidden Tasks list issued has been sent.
    const answered = page.waitForResponse(r =>
      r.url().includes('/api/defects') && r.url().includes('status=open'))
    await page.getByTestId('list-facet-status').click()
    await page.getByRole('option', { name: 'Open', exact: true }).click()
    await answered
    expect(stray).toEqual([])
  })
