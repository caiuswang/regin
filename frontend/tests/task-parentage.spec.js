/**
 * Naming a task as a run of another, from the browser (regin:63).
 *
 * The one act the feature grain had no SPA surface for: every other half
 * already rendered — a feature lists its runs, a run links back — but the
 * relationship they display could only be created from the CLI or the API.
 *
 * The picker's contents are the SERVER's answer. Two of the tests below stub
 * `/api/tasks/grain-candidates` with rows a client-side rule would have
 * filtered out, precisely to prove the browser applies no rule of its own; the
 * last two are live, because their claim is end-to-end — a feature built
 * entirely by mouse appears in the index and reads its own ledger.
 */
import { test, expect } from './auth-fixture.js'
import { API_BASE } from './helpers/api-base.js'

const PICKER = '[data-testid="task-parent-select"]'

function task(over = {}) {
  return {
    id: 7, title: 'the task being edited', body: '', status: 'idea',
    source: 'web', tags: [], links: [], priority: 'none', priority_rank: 4,
    project_id: null, parent_id: null, assignee: null, assignee_name: null,
    external_id: null, created_by: 1, created_at: '2026-09-14 10:00:00',
    updated_at: '2026-09-14 10:00:00', params: {}, verify_cmd: null,
    autorun: false, rounds: 0, images: [],
    ...over,
  }
}

async function stubDetail(page, row, grain = { children: 0, runs: 0 }) {
  await page.route(`**/api/tasks/${row.id}`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: { task: row, grain } })
      return
    }
    await route.fallback()
  })
}

async function stubCandidates(page, items) {
  await page.route('**/api/tasks/grain-candidates**', (route) =>
    route.fulfill({ json: { items, scanned: items.length, scan_limit: 300 } }))
}

async function quietSideReads(page) {
  for (const pattern of ['**/api/tasks/*/links*', '**/api/tasks/*/comments*',
                         '**/api/projects*']) {
    await page.route(pattern, (route) => route.fulfill({ json: { items: [] } }))
  }
}

async function pick(page, label) {
  await page.locator(PICKER).click()
  await page.getByRole('option', { name: label }).click()
}

// ── the three surfaces ───────────────────────────────────────

test('test_the_edit_sheet_writes_parent_id_through_the_existing_patch',
  async ({ page }) => {
    const row = task()
    await quietSideReads(page)
    await stubDetail(page, row)
    await stubCandidates(page, [{ id: 12, title: 'the feature',
                                  status: 'idea', parent_id: null }])

    const sent = []
    await page.route(`**/api/tasks/${row.id}`, async (route) => {
      if (route.request().method() !== 'PATCH') return route.fallback()
      sent.push(route.request().postDataJSON())
      await route.fulfill({
        json: { task: { ...row, parent_id: 12 },
                grain: { children: 0, runs: 0 } },
      })
    })

    await page.goto(`/tasks/${row.id}`)
    await page.getByTestId('task-detail-edit').click()
    await pick(page, '#12 the feature')
    await page.getByTestId('task-edit-save').click()

    await expect.poll(() => sent.length).toBe(1)
    expect(sent[0].parent_id).toBe(12)
  })

test('test_the_composer_can_capture_a_task_already_attached',
  async ({ page }) => {
    await quietSideReads(page)
    await stubCandidates(page, [{ id: 12, title: 'the feature',
                                  status: 'idea', parent_id: null }])
    await page.route('**/api/tasks?**', (route) =>
      route.fulfill({ json: { items: [], counts: null } }))

    const sent = []
    await page.route('**/api/tasks', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      sent.push(route.request().postDataJSON())
      await route.fulfill({ status: 201, json: { task: task({ id: 99 }) } })
    })

    await page.goto('/tasks')
    await page.getByTestId('task-new').click()
    await page.getByTestId('task-title').fill('a run of the feature')
    await pick(page, '#12 the feature')
    await page.getByTestId('task-submit').click()

    await expect.poll(() => sent.length).toBe(1)
    expect(sent[0].parent_id).toBe(12)
  })

test('test_the_runs_card_offers_an_attach_control', async ({ page }) => {
  const row = task()
  await quietSideReads(page)
  await stubDetail(page, row)
  await stubCandidates(page, [{ id: 21, title: 'a task that could be a run',
                                status: 'idea', parent_id: null }])

  await page.goto(`/tasks/${row.id}`)
  await expect(page.getByTestId('task-runs')).toBeVisible()
  await page.getByTestId('task-attach-run').click()

  const sent = []
  await page.route('**/api/tasks/21', async (route) => {
    sent.push(route.request().postDataJSON())
    await route.fulfill({
      json: { task: task({ id: 21, parent_id: row.id }),
              grain: { children: 0, runs: 0 } },
    })
  })
  await pick(page, '#21 a task that could be a run')
  await page.getByTestId('task-attach-save').click()

  await expect.poll(() => sent.length).toBe(1)
  expect(sent[0].parent_id).toBe(row.id)
})

test('test_clearing_the_parent_detaches_the_run', async ({ page }) => {
  const row = task({ parent_id: 12 })
  await quietSideReads(page)
  await stubDetail(page, row)
  await stubCandidates(page, [{ id: 12, title: 'the feature',
                                status: 'idea', parent_id: null }])

  const sent = []
  await page.route(`**/api/tasks/${row.id}`, async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    sent.push(route.request().postDataJSON())
    await route.fulfill({
      json: { task: { ...row, parent_id: null },
              grain: { children: 0, runs: 0 } },
    })
  })

  await page.goto(`/tasks/${row.id}`)
  await expect(page.getByTestId('task-parent')).toBeVisible()
  await page.getByTestId('task-detail-edit').click()
  await expect(page.locator(PICKER)).toContainText('#12 the feature')
  await pick(page, 'No parent (top-level)')
  await page.getByTestId('task-edit-save').click()

  await expect.poll(() => sent.length).toBe(1)
  expect(sent[0].parent_id).toBeNull()
})

// ── what the picker will and will not offer ──────────────────

test('test_the_candidate_list_is_computed_by_the_server_not_the_client',
  async ({ page }) => {
    // Every row here would be excluded by the rule spelled in the server: one
    // already has a parent, one holds runs of its own. The picker shows them
    // anyway, because it applies no rule — it renders the server's answer.
    const row = task()
    await quietSideReads(page)
    await stubDetail(page, row)
    await stubCandidates(page, [
      { id: 30, title: 'already a run', status: 'idea', parent_id: 12 },
      { id: 31, title: 'holds runs', status: 'done', parent_id: null },
    ])

    await page.goto(`/tasks/${row.id}`)
    await page.getByTestId('task-detail-edit').click()
    await page.locator(PICKER).click()
    await expect(page.getByRole('option')).toHaveCount(3)  // + top-level
    await expect(page.getByRole('option', { name: '#30 already a run' }))
      .toBeVisible()
  })

test('test_a_refused_parentage_renders_the_servers_own_sentence',
  async ({ page }) => {
    const row = task()
    const sentence = 'task 12 holds runs of its own, so it is a run and not '
      + 'a feature — a feature holds no runs, ever'
    await quietSideReads(page)
    await stubDetail(page, row)
    await stubCandidates(page, [{ id: 12, title: 'the feature',
                                  status: 'idea', parent_id: null }])
    await page.route(`**/api/tasks/${row.id}`, async (route) => {
      if (route.request().method() !== 'PATCH') return route.fallback()
      await route.fulfill({ status: 400,
                            json: { error: sentence, field: 'parent_id' } })
    })

    await page.goto(`/tasks/${row.id}`)
    await page.getByTestId('task-detail-edit').click()
    await pick(page, '#12 the feature')
    await page.getByTestId('task-edit-save').click()

    await expect(page.getByTestId('task-edit-error')).toHaveText(sentence)
  })

test('test_arm_is_not_offered_on_a_task_that_has_runs', async ({ page }) => {
  const feature = task({ id: 8, title: 'a feature with runs' })
  await quietSideReads(page)
  await stubCandidates(page, [])
  await stubDetail(page, feature, { children: 2, runs: 0 })
  await page.route('**/api/tasks?parent_id=8**', (route) =>
    route.fulfill({ json: { items: [task({ id: 9, parent_id: 8 })] } }))

  await page.goto('/tasks/8')
  await expect(page.getByTestId('task-runs')).toBeVisible()
  await expect(page.getByTestId('task-detail-arm')).toHaveCount(0)

  // And the same page on a plain task still offers it — the control is gone
  // where the server refuses, not everywhere.
  await stubDetail(page, task({ id: 10 }), { children: 0, runs: 0 })
  await page.goto('/tasks/10')
  await expect(page.getByTestId('task-detail-arm')).toBeVisible()
})

test('test_the_default_task_feed_asks_for_features', async ({ page }) => {
  // Observed, not stubbed: the claim is about what the page ASKS FOR, and a
  // fulfilled route would prove only what the stub answered. The browser
  // narrows to roots (regin:76); the server's own default is still every
  // task, which `tests/tasks/test_feature_run.py` pins.
  const asked = []
  page.on('request', (req) => {
    const url = new URL(req.url())
    // The counts read is the one the page always makes — an empty status group
    // mounts no feed of its own — and it carries the same filter set.
    if (url.pathname.startsWith('/api/tasks')) asked.push(url.searchParams)
  })

  const feedRead = page.waitForRequest(
    (req) => new URL(req.url()).pathname.startsWith('/api/tasks'))
  await page.goto('/tasks')
  await feedRead
  for (const params of asked) {
    expect(params.get('root_only')).toBe('1')
    expect(params.get('parent_id')).toBeNull()
  }
})

// ── the end-to-end claim, live ───────────────────────────────

test.describe('a feature built by mouse', () => {
  test.describe.configure({ mode: 'serial' })

  async function capture(page, title, parentLabel) {
    await page.goto('/tasks')
    await page.getByTestId('task-new').click()
    await page.getByTestId('task-title').fill(title)
    if (parentLabel) await pick(page, parentLabel)
    await page.getByTestId('task-submit').click()
    await expect(page.getByTestId('task-title')).toHaveCount(0)
    // Read back only — the building above is all mouse. The bearer token is
    // the one the fixture put in localStorage.
    const token = await page.evaluate(
      () => localStorage.getItem('regin_auth_token'))
    const feed = await page.request.get(
      `${API_BASE}/api/tasks?q=${encodeURIComponent(title)}`,
      { headers: { Authorization: `Bearer ${token}` } })
    const items = (await feed.json()).items
    return items.find(t => t.title === title)
  }

  test('test_attaching_a_run_in_the_browser_puts_the_feature_in_the_index',
    async ({ page }) => {
      const stamp = Date.now()
      const feature = await capture(page, `browser feature ${stamp}`)
      expect(feature).toBeTruthy()

      // The run is captured already attached, through the composer's picker.
      const run = await capture(page, `browser run ${stamp}`,
                                `#${feature.id} browser feature ${stamp}`)
      expect(run.parent_id).toBe(feature.id)

      await page.goto(`/tasks/${feature.id}`)
      await expect(page.getByTestId('task-runs')).toContainText(`#${run.id}`)

      await page.goto('/features')
      const search = page.getByLabel('Search features by key or title')
      await search.fill(`regin:${feature.id}`)
      await search.press('Enter')
      await expect(page.getByRole('link', { name: `regin:${feature.id}` }))
        .toBeVisible()
    })

  test('test_a_feature_built_entirely_in_the_browser_reads_its_own_ledger',
    async ({ page }) => {
      const stamp = Date.now()
      const feature = await capture(page, `ledger feature ${stamp}`)
      await capture(page, `ledger run ${stamp}`,
                    `#${feature.id} ledger feature ${stamp}`)

      // The done-claim, where CAI-274 put it — no second way to ship.
      await page.goto(`/tasks/${feature.id}`)
      await page.getByTestId('task-ship').click()
      // The claim has landed when the control offers to withdraw it — waiting
      // on the state rather than on the request keeps the read below honest.
      await expect(page.getByTestId('task-unclaim')).toBeVisible()

      await page.goto(`/features/regin:${feature.id}`)
      await expect(page.locator('body')).toContainText(`regin:${feature.id}`)
      await expect(page.locator('body')).toContainText('shipped')
    })
})
