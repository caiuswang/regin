/**
 * Feature #99 — a creation form started from something that already has a
 * project starts at that project, visibly and editably; and an armed run's
 * working directory starts at the task's own repo.
 *
 * Stubbed: every assertion is about what a form starts at and what it sends.
 * The server half of the carry is pinned in tests/features/test_project_carry.py.
 */
import { test, expect } from './auth-fixture.js'

const REPOS = [
  { id: 7, name: 'regin', path: '/r/regin' },
  { id: 9, name: 'atlas', path: '/r/atlas' },
]

async function stubRepos(page) {
  await page.route('**/api/repos', (route) =>
    route.fulfill({ json: { repos: REPOS } }))
}

async function stubFeatureIndex(page) {
  await page.route(/\/api\/features(\?.*)?$/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      json: {
        items: [], total: 0,
        pagination: { strategy: 'offset', total: 0, page: 0, size: 50,
                      has_next: false, has_prev: false },
        coverage: null, unattributed_defects: 0,
      },
    })
  })
}

function ledger(over = {}) {
  return {
    id: 242, title: 'Carry', origin: 'native', external_ref: null,
    external_url: null, sync_error: null, body: '',
    project_id: 7, project_name: 'regin',
    stage: { stage: 'building', blurb: 'being built', rounds: 1,
             claimed_at: null, open_defects: 0, escapes: 0 },
    synced_at: null, stage_live: true,
    cost: { usd: 0, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { gaps: {} }, tokens: {},
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0 },
    ...over,
  }
}

function task(over = {}) {
  return {
    id: 71, title: 'carry step', body: '', status: 'idea', source: 'web',
    tags: [], links: [], priority: 'none', priority_rank: 4,
    project_id: 9, feature_id: null, assignee: null, assignee_name: null,
    external_id: null, created_by: 1, created_at: '2026-09-14 10:00:00',
    updated_at: '2026-09-14 10:00:00', images: [], run_count: 0, armed: null,
    ...over,
  }
}

// ── New feature ──────────────────────────────────────────────

test('New feature pre-selects the scoped project and sends it', async ({ page }) => {
  await stubRepos(page)
  await stubFeatureIndex(page)
  let sent = null
  await page.route(/\/api\/features$/, (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    sent = route.request().postDataJSON()
    return route.fulfill({ status: 201, json: ledger({ id: 300 }) })
  })

  await page.goto('/features?project=7')
  await page.getByTestId('declare-feature').click()
  await expect(page.getByTestId('declare-project')).toContainText('regin')
  await page.getByTestId('declare-title').fill('scoped')
  await page.getByTestId('declare-submit').click()

  await expect.poll(() => sent).not.toBeNull()
  expect(sent.project_id).toBe(7)
})

test('New feature under "all" starts at No project and lists every repo',
  async ({ page }) => {
    await stubRepos(page)
    await stubFeatureIndex(page)
    await page.goto('/features?project=all')
    await page.getByTestId('declare-feature').click()

    const select = page.getByTestId('declare-project')
    await expect(select).toContainText('No project')
    await select.click()
    for (const repo of REPOS) {
      await expect(page.getByRole('option', { name: repo.name, exact: true }))
        .toBeVisible()
    }
  })

test('adopting also sends the picked project', async ({ page }) => {
  await stubRepos(page)
  await stubFeatureIndex(page)
  let sent = null
  await page.route(/\/api\/features$/, (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    sent = route.request().postDataJSON()
    return route.fulfill({ status: 201, json: ledger({ id: 301 }) })
  })

  await page.goto('/features?project=9')
  await page.getByTestId('declare-feature').click()
  await page.getByTestId('mode-adopt').click()
  await expect(page.getByTestId('declare-project')).toContainText('atlas')
  await page.getByTestId('adopt-ref').fill('CAI-1')
  await page.getByTestId('declare-submit').click()

  await expect.poll(() => sent).not.toBeNull()
  expect(sent).toMatchObject({ ref: 'CAI-1', project_id: 9 })
})

test('a scope naming an unlisted repo starts at No project and sends null',
  async ({ page }) => {
    await stubRepos(page)
    await stubFeatureIndex(page)
    let sent = null
    await page.route(/\/api\/features$/, (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      sent = route.request().postDataJSON()
      return route.fulfill({ status: 201, json: ledger({ id: 302 }) })
    })

    await page.goto('/features?project=404')
    await page.getByTestId('declare-feature').click()
    await expect(page.getByTestId('declare-project')).toContainText('No project')
    await page.getByTestId('declare-title').fill('stale scope')
    await page.getByTestId('declare-submit').click()

    await expect.poll(() => sent).not.toBeNull()
    expect(sent.project_id).toBeNull()
  })

// ── feature page ─────────────────────────────────────────────

async function stubLedger(page, body = ledger()) {
  await page.route(/\/api\/features\/\d+$/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({ json: body })
  })
  await page.route('**/api/tasks?**', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({ json: { items: [], next_cursor: null } })
  })
  await page.route('**/api/defects?feature_id=*', (route) =>
    route.fulfill({ json: { items: [], scope: 'feature' } }))
}

test('New task on a feature page shows and sends the carried project',
  async ({ page }) => {
    await stubRepos(page)
    await stubLedger(page)
    const posted = []
    await page.route('**/api/tasks', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      posted.push(route.request().postDataJSON())
      await route.fulfill({ status: 201, json: { task: task() } })
    })

    await page.goto('/features/242')
    await page.getByTestId('feature-new-task').click()
    await expect(page.getByTestId('feature-task-project')).toContainText('regin')

    // Editable: a multi-repo feature files a step under another repo.
    await page.getByTestId('feature-task-project').click()
    await page.getByRole('option', { name: 'atlas', exact: true }).click()
    await page.getByTestId('feature-task-title').fill('elsewhere')
    await page.getByTestId('feature-task-submit').click()

    await expect.poll(() => posted.length).toBe(1)
    expect(posted[0]).toMatchObject({ feature_id: 242, project_id: 9 })
  })

test('New task opened before the repos load still starts at the carried project',
  async ({ page }) => {
    let release
    const held = new Promise((resolve) => { release = resolve })
    await page.route('**/api/repos', async (route) => {
      await held
      await route.fulfill({ json: { repos: REPOS } })
    })
    await stubLedger(page)
    const posted = []
    await page.route('**/api/tasks', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      posted.push(route.request().postDataJSON())
      await route.fulfill({ status: 201, json: { task: task() } })
    })

    await page.goto('/features/242')
    await page.getByTestId('feature-new-task').click()
    release()
    await expect(page.getByTestId('feature-task-project')).toContainText('regin')
    await page.getByTestId('feature-task-title').fill('raced')
    await page.getByTestId('feature-task-submit').click()

    await expect.poll(() => posted.length).toBe(1)
    expect(posted[0].project_id).toBe(7)
  })

test('Report defect names the feature project, read-only', async ({ page }) => {
  await stubRepos(page)
  await stubLedger(page)
  await page.goto('/features/242')
  await page.getByTestId('feature-report-defect').click()
  await expect(page.getByTestId('feature-defect-project')).toContainText('regin')
})

// ── Tasks tab ────────────────────────────────────────────────

test('New task in the Tasks tab pre-selects the toolbar project',
  async ({ page }) => {
    await stubRepos(page)
    await page.goto('/tasks?project=7')
    await page.getByTestId('task-new').click()
    await expect(page.getByTestId('task-composer-project')).toContainText('regin')
  })

test('New task with no toolbar project starts at No project', async ({ page }) => {
  await stubRepos(page)
  await page.goto('/tasks?project=none')
  await page.getByTestId('task-new').click()
  await expect(page.getByTestId('task-composer-project')).toContainText('No project')
})

// ── Arm sheet ────────────────────────────────────────────────

async function stubArm(page, row) {
  await stubRepos(page)
  // The list groups by status, and a group is only drawn when its count says
  // it holds rows.
  await page.route('**/api/tasks/counts*', (route) => route.fulfill({ json: {
    total: 1, by_status: { idea: 1 }, by_priority: {}, by_project: {},
  } }))
  await page.route('**/api/tasks?**', (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({ json: {
      items: [row],
      pagination: { next_cursor: null, size: 50, has_next: false },
    } })
  })
  await page.route('**/api/agent-runs/launch-options', (route) =>
    route.fulfill({ json: {
      enabled: true, cwds: ['/r/regin', '/r/atlas'], models: ['opus'],
      permission_modes: ['default'], efforts: [],
      default_permission_mode: 'default',
    } }))
  let armed = null
  await page.route(`**/api/tasks/${row.id}/arm`, (route) => {
    armed = route.request().postDataJSON()
    return route.fulfill({ json: { task: { ...row, status: 'armed' } } })
  })
  return () => armed
}

test('the arm sheet seeds cwd from the task project, not the last arm',
  async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem(
      'regin.tasks.arm.last', JSON.stringify({ cwd: '/r/regin' })))
    await stubArm(page, task())
    await page.goto('/tasks')
    await page.locator('.task-row').first().getByTestId('task-arm').click()

    await expect(page.getByTestId('task-arm-form')).toBeVisible()
    await expect(page.getByTestId('task-arm-cwd')).toContainText('/r/atlas')
    await expect(page.getByTestId('task-arm-recalled')).toHaveCount(0)
    await expect(page.getByTestId('task-arm-cwd-mismatch')).toHaveCount(0)
  })

test('an arm cwd outside the task project is flagged, not refused',
  async ({ page }) => {
    const armed = await stubArm(page, task())
    await page.goto('/tasks')
    await page.locator('.task-row').first().getByTestId('task-arm').click()

    await page.getByTestId('task-arm-cwd').click()
    await page.getByRole('option', { name: '/r/regin', exact: true }).click()
    await expect(page.getByTestId('task-arm-cwd-mismatch'))
      .toContainText('Filed under atlas but will run in /r/regin')

    await page.getByTestId('task-arm-submit').click()
    await expect.poll(armed).not.toBeNull()
    expect(armed().cwd).toBe('/r/regin')
  })

test('a task with no project keeps the recalled cwd', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem(
    'regin.tasks.arm.last', JSON.stringify({ cwd: '/r/regin' })))
  await stubArm(page, task({ project_id: null }))
  await page.goto('/tasks')
  await page.locator('.task-row').first().getByTestId('task-arm').click()

  await expect(page.getByTestId('task-arm-cwd')).toContainText('/r/regin')
  await expect(page.getByTestId('task-arm-recalled')).toBeVisible()
})
