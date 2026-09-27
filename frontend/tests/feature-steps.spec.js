/**
 * Feature #102: a feature's steps are declared by kind, and the board names
 * the feature a step belongs to. Stubbed — every assertion is about what the
 * page renders and sends.
 */
import { test, expect } from './auth-fixture.js'

function ledger() {
  return {
    id: 242, title: 'Feature workbench', origin: 'native',
    external_ref: null, external_url: null, sync_error: null, body: 'spec',
    stage: { stage: 'spec', blurb: '', rounds: 1, claimed_at: null,
             open_defects: 0, escapes: 0 },
    synced_at: null, stage_live: true,
    cost: { usd: 0, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} },
    tokens: { input: 0, output: 0 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
  }
}

function task(over = {}) {
  return {
    id: 71, title: 'a step', body: '', status: 'idea', kind: 'task',
    source: 'web', tags: [], links: [], priority: 'none', priority_rank: 4,
    project_id: null, feature_id: 242, feature_title: 'Feature workbench',
    assignee: null, assignee_name: null, external_id: null, created_by: 1,
    created_at: '2026-09-14 10:00:00', updated_at: '2026-09-14 10:00:00',
    images: [], run_count: 0, ...over,
  }
}

async function stubFeature(page, tasks) {
  await page.route(/\/api\/features\/\d+$/, (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: ledger() }) : route.fallback())
  await page.route('**/api/tasks?**', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { items: tasks, next_cursor: null } })
      : route.fallback())
  await page.route('**/api/defects?feature_id=*', (route) =>
    route.fulfill({ json: { items: [], scope: 'feature' } }))
}

test('Write spec creates the step with the instruction as its body',
  async ({ page }) => {
    const posted = []
    await stubFeature(page, [])
    await page.route('**/api/features/242/steps', async (route) => {
      posted.push(route.request().postDataJSON())
      await route.fulfill({ status: 201, json: {
        task: task({ id: 90, kind: 'spec', title: 'Spec · Feature workbench',
                     external_id: 'feature:242:spec' }), created: true } })
    })
    await page.goto('/features/242')

    await page.getByTestId('feature-verb-spec').click()
    await page.getByTestId('feature-step-body').fill('settle the key shape')
    await page.getByTestId('feature-step-submit').click()

    await expect(page).toHaveURL(/\/tasks\/90$/)
    expect(posted).toEqual([{ kind: 'spec', body: 'settle the key shape' }])
  })

test('Build opens the existing build step instead of creating one',
  async ({ page }) => {
    let posts = 0
    await stubFeature(page, [
      task({ id: 5, kind: 'build', title: 'S1 slice', external_id: 'S1' }),
      task({ id: 6, kind: 'build', title: 'Build · Feature workbench',
             external_id: 'feature:242:build' }),
    ])
    await page.route('**/api/features/242/steps', (route) => {
      posts += 1
      return route.fulfill({ status: 500, json: {} })
    })
    await page.goto('/features/242')

    await page.getByTestId('feature-verb-build').click()

    await expect(page).toHaveURL(/\/tasks\/6$/)
    expect(posts).toBe(0)
  })

test('steps list spec, then build, then fix, then free-form, with kind chips',
  async ({ page }) => {
    await stubFeature(page, [
      task({ id: 1, kind: 'task', title: 'free' }),
      task({ id: 2, kind: 'fix', title: 'a bug' }),
      task({ id: 3, kind: 'build', title: 'the build' }),
      task({ id: 4, kind: 'spec', title: 'the spec' }),
    ])
    await page.goto('/features/242')

    const names = page.getByTestId('feature-tasks-list').locator('.ft-name')
    await expect(names).toHaveText(['the spec', 'the build', 'a bug', 'free'])
    await expect(page.getByTestId('feature-tasks-list')
      .getByTestId('task-kind-chip')).toHaveText(['spec', 'build', 'fix'])
  })

test('the list shows feature steps by default, and names the feature',
  async ({ page }) => {
    await page.route('**/api/tasks?**', (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ json: { items: [
            task({ id: 71, title: 'Enrich the feature' }),
            task({ id: 72, feature_id: null, feature_title: null,
                   title: 'unfeatured' }),
          ], next_cursor: null, counts: {} } })
        : route.fallback())
    await page.route('**/api/tasks/counts?**', (route) =>
      route.fulfill({ json: { total: 2, by_status: { idea: 2 },
                                     by_priority: {}, by_project: {} } }))
    const asked = []
    page.on('request', (req) => {
      const url = new URL(req.url())
      if (url.pathname === '/api/tasks') asked.push(url.searchParams)
    })
    await page.goto('/tasks?group=none')

    await expect(page.getByTestId('task-chip-unfeatured')).toHaveCount(0)
    const row = page.locator('#task-71')
    await expect(row.getByTestId('task-feature-line'))
      .toHaveText('#242 Feature workbench')
    await expect(row.getByTestId('task-title-secondary'))
      .toHaveText('Enrich the feature')
    await expect(page.locator('#task-72').getByTestId('task-feature-line'))
      .toHaveCount(0)
    expect(asked.length).toBeGreaterThan(0)
    expect(asked.every(p => !p.get('root_only'))).toBe(true)
  })

test('unchecking Feature steps narrows to tasks under no feature',
  async ({ page }) => {
    await page.route('**/api/tasks?**', (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ json: { items: [], next_cursor: null, counts: {} } })
        : route.fallback())
    await page.route('**/api/tasks/counts?**', (route) =>
      route.fulfill({ json: { total: 0, by_status: {}, by_priority: {},
                              by_project: {} } }))
    const feed = (rootOnly) => page.waitForRequest((req) => {
      const url = new URL(req.url())
      return url.pathname === '/api/tasks'
        && (url.searchParams.get('root_only') || '') === rootOnly
    })
    await page.goto('/tasks?group=none')
    await expect(page.getByTestId('task-chip-unfeatured')).toHaveCount(0)

    const narrowed = feed('1')
    await page.getByTestId('task-filter-button').click()
    await page.getByTestId('task-steps-toggle').click()
    await narrowed
    await page.keyboard.press('Escape')
    await expect(page).toHaveURL(/unfeatured=1/)
    await expect(page.getByTestId('task-chip-unfeatured'))
      .toContainText('No feature only')

    const widened = feed('')
    await page.getByTestId('task-chip-remove-unfeatured').click()
    await widened
    await expect(page).not.toHaveURL(/unfeatured=/)
    await expect(page.getByTestId('task-chip-unfeatured')).toHaveCount(0)
  })

test('+ task declares its stage, and fix is not offered', async ({ page }) => {
  const posted = []
  await stubFeature(page, [])
  await page.route('**/api/projects**', (route) =>
    route.fulfill({ json: { items: [] } }))
  await page.route('**/api/tasks', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    posted.push(route.request().postDataJSON())
    await route.fulfill({ status: 201, json: {
      task: task({ id: 91, kind: 'spec', title: 'draft the API' }) } })
  })
  await page.goto('/features/242')

  await page.getByTestId('feature-new-task').click()
  await page.getByTestId('feature-task-title').fill('draft the API')
  await page.getByTestId('feature-task-kind').click()
  const options = page.getByRole('option')
  await expect(options).toHaveText(['Task', 'Spec', 'Build'])
  await options.filter({ hasText: 'Spec' }).click()
  await page.getByTestId('feature-task-submit').click()

  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0]).toMatchObject({ title: 'draft the API', kind: 'spec',
                                    feature_id: 242 })
})
