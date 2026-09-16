/**
 * The feature page as the pipeline's single workbench.
 *
 * Three edges that were already closed in the data layer and open in the
 * browser: the spec the feature was declared with, the tasks that are its
 * steps, and the defects blamed on it. Stubbed, because every assertion is
 * about what the page renders and what it sends — a live database would turn
 * a rendering regression into an empty list.
 *
 * The defects block is deliberately the SAME component the task page mounts.
 * The one reading it adds is a feature with no task at all: a shape the model
 * allows and can ship, which until now had no way to report a bug from the
 * browser at all.
 */
import { test, expect } from './auth-fixture.js'

const SPEC = '## Intent\nthree edges the data layer already has'

function ledger(over = {}) {
  return {
    id: 242, title: 'Feature workbench', origin: 'native',
    external_ref: null, external_url: null, sync_error: null,
    body: SPEC,
    stage: {
      stage: 'building', blurb: 'being built',
      rounds: 1, claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true,
    cost: { usd: 41.9, sessions: 3, recalled: 1, shared: 0, gaps: {} },
    work_time: { active_work_ms: 5_400_000, gaps: {} },
    tokens: { input: 1200, output: 3400 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: {
      declared_sessions: 13, all_sessions: 2935,
      declared_usd: 57.24, all_usd: 23251.49,
    },
    ...over,
  }
}

function task(over = {}) {
  return {
    id: 71, title: 'wire the spec block', body: '', status: 'idea',
    source: 'web', tags: [], links: [], priority: 'none', priority_rank: 4,
    project_id: null, feature_id: 242, assignee: null, assignee_name: null,
    external_id: null, created_by: 1, created_at: '2026-09-14 10:00:00',
    updated_at: '2026-09-14 10:00:00', images: [], run_count: 0,
    ...over,
  }
}

function defect(over = {}) {
  return {
    id: 149, feature_id: 242, feature_title: 'Feature workbench',
    symptom: 'the spec is write-only from the browser',
    status: 'open', round: 1, blamed_event_kind: null, missing_check: null,
    suspect_paths: [], fix_task_id: null,
    opened_at: '2026-09-14 12:00:00', closed_at: null,
    ...over,
  }
}

/** Stub the three reads the page fires on mount. */
async function stub(page, { body = ledger(), tasks = [], defects = [] } = {}) {
  await page.route(/\/api\/features\/\d+$/, async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({ json: body })
  })
  await page.route('**/api/tasks?**', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({ json: { items: tasks, next_cursor: null } })
  })
  await page.route('**/api/defects?feature_id=*', (route) =>
    route.fulfill({ json: { items: defects, scope: 'feature' } }))
}

async function asViewer(page) {
  await page.addInitScript(() => {
    const raw = localStorage.getItem('regin_auth_user')
    if (raw) {
      const user = JSON.parse(raw)
      user.role = 'viewer'
      localStorage.setItem('regin_auth_user', JSON.stringify(user))
    }
  })
}

// ── spec ──────────────────────────────────────────────────────

test('the feature page shows the spec it was declared with', async ({ page }) => {
  await stub(page)
  await page.goto('/features/242')

  await expect(page.getByTestId('feature-spec')).toContainText(
    'three edges the data layer already has')
})

test('a feature declared with no spec says so rather than rendering blank',
  async ({ page }) => {
    await stub(page, { body: ledger({ body: null }) })
    await page.goto('/features/242')

    await expect(page.getByTestId('feature-spec-empty')).toBeVisible()
  })

test('the spec can be edited in place', async ({ page }) => {
  const patched = []
  await stub(page)
  await page.route(/\/api\/features\/\d+$/, async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    patched.push(route.request().postDataJSON())
    await route.fulfill({ json: ledger({ body: '## Intent\nrewritten' }) })
  })
  await page.goto('/features/242')

  await page.getByTestId('feature-spec-edit').click()
  await page.getByTestId('feature-spec-input').fill('## Intent\nrewritten')
  await page.getByTestId('feature-spec-save').click()

  expect(patched).toEqual([{ body: '## Intent\nrewritten' }])
  // Read back from the response, not from the local draft: what the page
  // shows after a save must be what the server stored.
  await expect(page.getByTestId('feature-spec')).toContainText('rewritten')
  await expect(page.getByTestId('feature-spec-input')).toHaveCount(0)
})

// ── tasks ─────────────────────────────────────────────────────

test('the feature page lists its tasks and links to each', async ({ page }) => {
  const asked = []
  await stub(page)
  await page.route('**/api/tasks?**', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    asked.push(route.request().url())
    await route.fulfill({
      json: {
        items: [task(), task({ id: 72, title: 'move the defects card' })],
        next_cursor: null,
      },
    })
  })
  await page.goto('/features/242')

  const list = page.getByTestId('feature-tasks-list')
  await expect(list).toContainText('wire the spec block')
  await expect(list).toContainText('move the defects card')
  await expect(list.locator('a').first()).toHaveAttribute('href', '/tasks/71')
  // Scoped server-side. A client-side filter over one page answers a
  // different question than the heading claims.
  expect(asked.some((u) => u.includes('feature_id=242'))).toBe(true)
})

test('a feature with no task says so rather than hiding the block',
  async ({ page }) => {
    await stub(page)
    await page.goto('/features/242')

    await expect(page.getByTestId('feature-tasks-empty')).toBeVisible()
  })

test('a task created here arrives under this feature', async ({ page }) => {
  const posted = []
  const stored = []
  await stub(page)
  await page.route('**/api/tasks?**', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await route.fulfill({ json: { items: stored.slice(), next_cursor: null } })
  })
  await page.route('**/api/tasks', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    const sent = route.request().postDataJSON()
    posted.push(sent)
    const row = task({ title: sent.title })
    stored.push(row)
    await route.fulfill({ status: 201, json: { task: row } })
  })
  await page.goto('/features/242')

  await expect(page.getByTestId('feature-tasks-empty')).toBeVisible()
  await page.getByTestId('feature-new-task').click()
  await page.getByTestId('feature-task-title').fill('wire the spec block')
  await page.getByTestId('feature-task-submit').click()

  expect(posted).toHaveLength(1)
  expect(posted[0].title).toBe('wire the spec block')
  expect(posted[0].feature_id).toBe(242)
  // The list is the record. A created task that does not appear in it is
  // indistinguishable from one that was dropped.
  await expect(page.getByTestId('feature-tasks-list'))
    .toContainText('wire the spec block')
})

// ── defects ───────────────────────────────────────────────────

test('the feature page lists the defects blamed on it', async ({ page }) => {
  await stub(page, {
    defects: [defect(), defect({ id: 150, symptom: 'the tasks block is absent' })],
  })
  await page.goto('/features/242')

  const list = page.getByTestId('feature-defects-list')
  await expect(list).toContainText('the spec is write-only from the browser')
  await expect(list).toContainText('the tasks block is absent')
  await expect(list).toContainText('#149')
})

test('a defect can be reported from a feature with no task', async ({ page }) => {
  const posted = []
  const stored = []
  await stub(page)
  await page.route('**/api/defects?feature_id=*', (route) =>
    route.fulfill({ json: { items: stored.slice(), scope: 'feature' } }))
  await page.route('**/api/defects', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    posted.push(route.request().postDataJSON())
    const row = defect({ symptom: posted[posted.length - 1].symptom })
    stored.push(row)
    await route.fulfill({ status: 201, json: { outcome: 'opened', defect: row } })
  })
  await page.goto('/features/242')

  // The shape this whole edge exists for: no task, and a bug to report anyway.
  await expect(page.getByTestId('feature-tasks-empty')).toBeVisible()
  await expect(page.getByTestId('feature-defects-empty')).toBeVisible()

  await page.getByTestId('feature-report-defect').click()
  await page.getByTestId('feature-defect-symptom').fill('the gutter folds')
  await page.getByTestId('feature-defect-submit').click()

  expect(posted[0]).toEqual({ symptom: 'the gutter folds', feature_id: 242 })
  await expect(page.getByTestId('feature-defect-result')).toContainText('#149')
  await expect(page.getByTestId('feature-defects-list'))
    .toContainText('the gutter folds')
})

// ── role ──────────────────────────────────────────────────────

test('a viewer sees no edit, no new task and no report control',
  async ({ page }) => {
    await asViewer(page)
    await stub(page, { tasks: [task()], defects: [defect()] })
    await page.goto('/features/242')

    // Every read still renders — the split guards write-accidents, not
    // read-secrets.
    await expect(page.getByTestId('feature-spec')).toContainText(
      'three edges the data layer already has')
    await expect(page.getByTestId('feature-tasks-list')).toContainText(
      'wire the spec block')
    await expect(page.getByTestId('feature-defects-list')).toContainText('#149')

    await expect(page.getByTestId('feature-spec-edit')).toHaveCount(0)
    await expect(page.getByTestId('feature-new-task')).toHaveCount(0)
    await expect(page.getByTestId('feature-report-defect')).toHaveCount(0)
  })

// ── the defects head ──────────────────────────────────────────

test('the defects scope link appears only when there is a defect to scope to',
  async ({ page }) => {
    await stub(page)
    await page.goto('/features/242')

    await expect(page.getByTestId('feature-defects-empty')).toBeVisible()
    // A link into a filtered list that is empty by construction: it promises
    // rows and lands on none.
    await expect(page.getByTestId('feature-defects-scope')).toHaveCount(0)

    await stub(page, { defects: [defect()] })
    await page.reload()
    await expect(page.getByTestId('feature-defects-scope'))
      .toHaveAttribute('href', '/defects?feature_id=242')
  })

test('the defects head centres its title against its controls',
  async ({ page }) => {
    await stub(page, { defects: [defect()] })
    await page.goto('/features/242')

    const head = page.getByTestId('feature-defects').locator('.td-head')
    const title = head.locator('h2')
    const link = page.getByTestId('feature-defects-scope')
    const button = page.getByTestId('feature-report-defect')
    const [h, t, l, b] = await Promise.all(
      [head, title, link, button].map((el) => el.boundingBox()))

    // The heading's own bottom margin is what pushed it off the row's centre.
    expect(Math.abs((t.y + t.height / 2) - (l.y + l.height / 2)))
      .toBeLessThanOrEqual(1)
    // and made the row taller than its tallest control.
    expect(h.height).toBeLessThanOrEqual(Math.max(t.height, b.height) + 1)
  })
