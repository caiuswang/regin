/**
 * A task is the feature; Run is the declaration (regin:76).
 *
 * The list asks the server for features (`root_only=1`) unless told to show
 * runs, unfolds a feature's runs beneath it on demand, and names how many
 * attempts a task's own runs amount to. The detail page can start a run and
 * links both grains to the feature ledger.
 *
 * Every response below is stubbed: the claims are about what the browser
 * asks for and renders, not about the server's answer.
 */
import { test, expect } from './auth-fixture.js'

const FLAT_VIEW = {
  'regin.tasks.group': 'none',
  'regin.tasks.status': '',
  'regin.tasks.archived': '',
  'regin.tasks.priority': '',
  'regin.tasks.project': '',
  'regin.tasks.sort': 'created_desc',
  'regin.tasks.runs': '',
}

function task(over = {}) {
  return {
    id: 7, title: 'the feature', body: '', status: 'idea',
    source: 'web', tags: [], links: [], priority: 'none', priority_rank: 4,
    project_id: null, parent_id: null, assignee: null, assignee_name: null,
    external_id: null, created_by: 1, created_at: '2026-09-14 10:00:00',
    updated_at: '2026-09-14 10:00:00', params: {}, verify_cmd: null,
    autorun: false, rounds: 1, images: [], child_count: 0, run_count: 0,
    ...over,
  }
}

const COUNTS = {
  by_status: { idea: 1 }, by_priority: { none: 1 }, by_project: { none: 1 },
  total: 1,
}

function isList(request) {
  const url = new URL(request.url())
  return request.method() === 'GET' && url.pathname.endsWith('/api/tasks')
}

async function stubList(page, rows, byParent = {}) {
  const asked = []
  await page.route('**/api/tasks?**', async (route) => {
    if (!isList(route.request())) return route.fallback()
    const url = new URL(route.request().url())
    asked.push(url.searchParams)
    const parent = url.searchParams.get('parent_id')
    const items = parent ? (byParent[parent] || []) : rows
    await route.fulfill({ json: { items, next_cursor: null, counts: COUNTS,
                                  status_filter: '' } })
  })
  await page.route('**/api/tasks/counts**', (route) =>
    route.fulfill({ json: COUNTS }))
  await page.route('**/api/projects**', (route) =>
    route.fulfill({ json: { items: [] } }))
  return asked
}

async function stubDetail(page, row, grain = { children: 0, runs: 0 }) {
  await page.route(`**/api/tasks/${row.id}`, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({ json: { task: row, grain } })
  })
  for (const pattern of ['**/api/tasks/*/links*', '**/api/tasks/*/comments*',
                         '**/api/projects*']) {
    await page.route(pattern, (route) => route.fulfill({ json: { items: [] } }))
  }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((state) => {
    for (const [key, value] of Object.entries(state)) {
      localStorage.setItem(key, value)
    }
  }, FLAT_VIEW)
})

// ── the list ─────────────────────────────────────────────────

test('test_the_list_asks_for_roots_by_default_and_show_runs_lifts_it',
  async ({ page }) => {
    const asked = await stubList(page, [task()])

    await page.goto('/tasks')
    await expect(page.getByTestId('task-row')).toHaveCount(1)
    expect(asked[0].get('root_only')).toBe('1')

    await page.getByTestId('task-runs-toggle').click()
    await expect.poll(() => asked.at(-1).get('root_only')).toBeNull()
    await expect(page.getByTestId('task-runs-toggle'))
      .toHaveAttribute('aria-pressed', 'true')
  })

test('test_a_feature_row_expands_to_its_runs', async ({ page }) => {
  await stubList(page, [task({ child_count: 2, status: 'running' })], {
    7: [task({ id: 8, title: 'first run', parent_id: 7, status: 'done' }),
        task({ id: 9, title: 'second run', parent_id: 7, status: 'running' })],
  })
  await page.route('**/api/tasks/*/runs', (route) =>
    route.fulfill({ json: { runs: [], feature: true } }))

  await page.goto('/tasks')
  await expect(page.getByTestId('task-child-count')).toHaveText('2 runs')
  await expect(page.getByTestId('task-row')).toHaveCount(1)

  await page.getByTestId('task-expand').click()

  const nested = page.getByTestId('task-children-7').getByTestId('task-row')
  await expect(nested).toHaveCount(2)
  await expect(nested.nth(0)).toHaveAttribute('data-parent', '7')
  await expect(nested.nth(0)).toContainText('first run')
  await expect(nested.nth(0).getByTestId('task-arm')).toHaveCount(0)
  await expect(page.getByTestId('task-expand'))
    .toHaveAttribute('aria-expanded', 'true')

  await page.getByTestId('task-expand').click()
  await expect(page.getByTestId('task-children-7')).toHaveCount(0)
})

test('test_a_task_with_attempts_names_them_and_opens_its_history',
  async ({ page }) => {
    await stubList(page, [task({ run_count: 3, status: 'done' })])
    await page.route('**/api/tasks/7/runs', (route) =>
      route.fulfill({ json: { feature: false, runs: [
        { id: 1, task_id: 7, attempt: 3, status: 'done', trace_id: 'sdk-3',
          started_at: '2026-09-14 12:00:00', ended_at: '2026-09-14 12:10:00',
          exit_reason: null, verify_exit_code: null, live: false },
      ] } }))

    await page.goto('/tasks')
    await expect(page.getByTestId('task-run-count')).toHaveText('3 attempts')
    await expect(page.getByTestId('task-expand')).toHaveCount(0)

    await page.getByTestId('task-run-count').click()

    await expect(page.getByTestId('task-runs')).toBeVisible()
    await expect(page.getByTestId('task-run-trace-3'))
      .toHaveAttribute('href', '/trace/sessions/sdk-3')
  })

// ── the detail page ──────────────────────────────────────────

test('test_the_detail_page_offers_run_on_a_feature', async ({ page }) => {
  const row = task({ status: 'armed' })
  await stubDetail(page, row)
  const started = []
  await page.route('**/api/tasks/7/run', async (route) => {
    started.push(route.request().method())
    await route.fulfill({ status: 201, json: { run: { id: 1, attempt: 1 } } })
  })

  await page.goto('/tasks/7')
  await expect(page.getByTestId('task-detail-arm')).toBeVisible()
  await page.getByTestId('task-run').click()

  await expect.poll(() => started).toEqual(['POST'])
})

test('test_a_run_and_a_feature_both_link_to_the_ledger', async ({ page }) => {
  await stubDetail(page, task({ id: 8, title: 'a run', parent_id: 7 }))
  await page.goto('/tasks/8')
  await expect(page.getByTestId('task-parent-ledger'))
    .toHaveAttribute('href', '/features/regin:7')
  await expect(page.getByTestId('task-ship')).toHaveCount(0)

  await stubDetail(page, task({ status: 'done', claimed_at: null }), { children: 0, runs: 2 })
  await page.goto('/tasks/7')
  await expect(page.getByTestId('task-own-runs')).toContainText('2 attempts')
  await expect(page.getByTestId('task-feature-ledger'))
    .toHaveAttribute('href', '/features/regin:7')
  await expect(page.getByTestId('task-ship')).toBeVisible()
  await expect(page.getByTestId('task-attach-run')).toHaveCount(0)
})
