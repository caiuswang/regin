/**
 * The /tasks surfaces that only exist once a backlog stops being a demo:
 * grouping, server-side search and filters, bulk edits, the `#id` address,
 * the detail route, and projects.
 *
 * The claim under test throughout is that the SERVER answers the control. A
 * group header counted from loaded rows reports the size of the page, and a
 * collapsed group reports zero — so every count assertion here is checked
 * against what the API itself says, never against a number the page could
 * have made up from what it happened to have.
 */
import { fileURLToPath } from 'node:url'
import { test, expect } from './auth-fixture.js'

test.describe.configure({ mode: 'serial' })

const SFX = 'lin'

// Seeded through the browser's own credential so the rows are indistinguishable
// from ones a user captured — a fixture that writes straight to SQLite would
// not exercise the route the page actually posts to.
async function api(page, method, path, body) {
  return page.evaluate(async ([m, p, b]) => {
    const res = await fetch(`/api${p}`, {
      method: m,
      headers: {
        Authorization: `Bearer ${localStorage.getItem('regin_auth_token')}`,
        ...(b ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(b ? { body: JSON.stringify(b) } : {}),
    })
    return { status: res.status, body: await res.json() }
  }, [method, path, body])
}

async function forceFlat(page) {
  await page.addInitScript(() => {
    localStorage.setItem('regin.tasks.group', 'none')
    localStorage.setItem('regin.tasks.status', '')
    localStorage.setItem('regin.tasks.archived', '')
    localStorage.setItem('regin.tasks.priority', '')
    localStorage.setItem('regin.tasks.project', '')
    localStorage.setItem('regin.tasks.sort', 'created_desc')
  })
}

async function forceGrouped(page, by = 'status') {
  await page.addInitScript((g) => {
    localStorage.setItem('regin.tasks.group', g)
    localStorage.setItem('regin.tasks.status', '')
    localStorage.setItem('regin.tasks.archived', '')
    localStorage.setItem('regin.tasks.priority', '')
    localStorage.setItem('regin.tasks.project', '')
    localStorage.setItem('regin.tasks.sort', 'created_desc')
  }, by)
}

let seeded = null

// A task's project IS a registered repo — the same thing `project_id` means
// in `pattern_deployments`. There is no second registry to seed.
//
// Resolved rather than created blind: a retry re-runs this against rows the
// previous attempt already left behind, and a repo name is UNIQUE — so a bare
// POST fails and every later `project.id` read is undefined.
async function ensureProject(page) {
  const repos = (await api(page, 'GET', '/repos')).body.repos
  if (repos.length) return repos[0]
  // The repo root, not `process.cwd()` — the runner's cwd is `frontend/`, and
  // `add_repo` registers a git root.
  const root = fileURLToPath(new URL('../..', import.meta.url))
  return (await api(page, 'POST', '/repos',
    { path: root.replace(/\/$/, '') })).body.repo
}

async function ensureTask(page, title, fields) {
  const found = (await api(page, 'GET',
    `/tasks?include_archived=1&q=${encodeURIComponent(title)}`))
    .body.items.find(t => t.title === title)
  if (found) return found
  return (await api(page, 'POST', '/tasks',
    { title, body: `body of ${title}`, ...fields })).body.task
}

test.beforeEach(async ({ page }) => {
  await page.goto('/tasks')
  if (seeded) return
  const project = await ensureProject(page)
  const ids = {}
  for (const [name, extra] of [
    ['alpha', { priority: 'urgent', project_id: project.id, tags: ['ui'] }],
    ['beta', { priority: 'low', tags: ['ui-polish'] }],
    ['gamma', { priority: 'medium', project_id: project.id }],
  ]) {
    ids[name] = (await ensureTask(page, `${SFX}-${name}`, extra)).id
  }
  // Armed, so the backlog holds more than one status: a group-per-status view
  // and a "two statuses must look different" assertion both need at least two.
  await api(page, 'POST', `/tasks/${ids.gamma}/arm`, { cwd: '.' })
  seeded = { project, ids }
})

// The view is a lazy route chunk, so its keyboard listener does not exist the
// moment `goto` resolves — a chord pressed before the filter bar renders lands
// on nothing at all.
async function listReady(page) {
  await expect(page.getByTestId('list-search')).toBeVisible()
}

// ── the identifier ───────────────────────────────────────────

test('every row shows its id, and the id links to that task', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)
  const row = page.locator('[data-testid="task-row"]', { hasText: `${SFX}-alpha` }).first()
  await expect(row).toBeVisible()
  const link = row.getByTestId('task-id')
  await expect(link).toHaveText(`#${seeded.ids.alpha}`)
  await expect(link).toHaveAttribute('href', `/tasks/${seeded.ids.alpha}`)
})

test('the detail route resolves one task directly, not by scanning a page',
  async ({ page }) => {
    const calls = []
    page.on('request', r => {
      const u = new URL(r.url())
      if (u.pathname.startsWith('/api/tasks')) calls.push(u.pathname + u.search)
    })
    await page.goto(`/tasks/${seeded.ids.alpha}`)
    await expect(page.locator('.page-title')).toContainText(`${SFX}-alpha`)
    await expect(page.locator('.page-title')).toContainText(`#${seeded.ids.alpha}`)
    // A list read filtered client-side would silently stop working at whatever
    // page size it happened to ask for.
    expect(calls.some(c => c === `/api/tasks/${seeded.ids.alpha}`)).toBe(true)
    expect(calls.some(c => c.startsWith('/api/tasks?'))).toBe(false)
  })

test('an unknown id says so instead of rendering an empty page', async ({ page }) => {
  await page.goto('/tasks/999999')
  await expect(page.getByTestId('task-detail-error')).toContainText('#999999')
})

// ── status is not conveyed by colour alone ───────────────────

test('each status carries its name and its own shape', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)
  const icon = page.locator('[data-testid="task-row"]').first().locator('svg[data-status]')
  await expect(icon).toHaveAttribute('aria-label', /\w+/)

  // A greyscale reader must still be able to tell two states apart, so the
  // geometry — not just the fill — has to differ.
  const shapes = await page.locator('svg[data-status]').evaluateAll(els => {
    const byStatus = {}
    for (const el of els) byStatus[el.dataset.status] = el.innerHTML.replace(/<title>.*?<\/title>/, '')
    return byStatus
  })
  const drawings = Object.values(shapes)
  expect(Object.keys(shapes).length).toBeGreaterThan(1)
  expect(new Set(drawings).size).toBe(drawings.length)
})

// ── server-side search ───────────────────────────────────────

test('search filters on the server and the total follows', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)

  const requests = []
  page.on('request', r => {
    const u = new URL(r.url())
    if (u.pathname === '/api/tasks') requests.push(u.searchParams.get('q'))
  })

  await page.getByTestId('list-search').fill(`${SFX}-alpha`)
  // Enter commits the search. There is no debounce to wait out: a clock
  // deciding when a search happened is exactly what `q` living in the URL
  // rules out — it would be one history write per keystroke.
  await page.getByTestId('list-search').press('Enter')
  await expect(page.locator('[data-testid="task-row"]')).toHaveCount(1)
  await expect(page.getByTestId('list-count')).toHaveText(/^1 of [0-9]+ tasks?$/)
  // The narrowing was a query, not an Array.filter over the loaded page.
  expect(requests).toContain(`${SFX}-alpha`)
})

test('a tag filter matches whole tags, not prefixes', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)
  const scoped = await api(page, 'GET', '/tasks?tag=ui')
  const titles = scoped.body.items.map(t => t.title)
  expect(titles).toContain(`${SFX}-alpha`)
  expect(titles).not.toContain(`${SFX}-beta`)
})

test('a priority filter narrows the list and the reset clears it', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)
  await page.getByTestId('task-filter-button').click()
  await page.getByTestId('task-filter-priority-urgent').click()
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-testid="task-row"]',
    { hasText: `${SFX}-beta` })).toHaveCount(0)

  await page.getByTestId('task-chip-remove-priority').click()
  await expect(page.locator('[data-testid="task-row"]',
    { hasText: `${SFX}-beta` })).toHaveCount(1)
})

// ── grouping ─────────────────────────────────────────────────

test('group headers carry the server total, not the loaded count',
  async ({ page }) => {
    await forceGrouped(page, 'status')
    await page.goto('/tasks')
    await listReady(page)

    const counts = (await api(page, 'GET', '/tasks/counts')).body
    const header = page.getByTestId('task-group-count-idea')
    await expect(header).toHaveText(String(counts.by_status.idea))

    // Collapsing must not change the number: a header counted from rendered
    // rows would read 0 here.
    await page.getByTestId('task-group-toggle-idea').click()
    await expect(page.getByTestId('task-group-count-idea'))
      .toHaveText(String(counts.by_status.idea))
  })

test('a group with nothing in it is not rendered at all', async ({ page }) => {
  await forceGrouped(page, 'status')
  await page.goto('/tasks')
  await listReady(page)
  const counts = (await api(page, 'GET', '/tasks/counts')).body
  for (const [status, n] of Object.entries(counts.by_status)) {
    // Archived has its own rule below — its count is honest, but the group is
    // only shown to someone who asked for the archive.
    if (status === 'archived') continue
    await expect(page.getByTestId(`task-group-${status}`))
      .toHaveCount(n > 0 ? 1 : 0)
  }
})

test('the archived chip counts what asking for archived returns',
  async ({ page }) => {
    await forceFlat(page)
    await page.goto('/tasks')
    await listReady(page)

    const target = (await api(page, 'GET',
      `/tasks?q=${SFX}-beta`)).body.items[0]
    await api(page, 'PATCH', `/tasks/${target.id}`, { status: 'archived' })

    const counted = (await api(page, 'GET', '/tasks/counts'))
      .body.by_status.archived
    const listed = (await api(page, 'GET', '/tasks?status=archived'))
      .body.items.length
    // A chip reading 0 over a group that has rows is how the archive becomes
    // unreachable — the group is then hidden for being "empty".
    expect(counted).toBe(listed)
    expect(counted).toBeGreaterThan(0)

    await api(page, 'PATCH', `/tasks/${target.id}`, { status: 'idea' })
  })

test('archived is a world you enter, not one mixed into the default view',
  async ({ page }) => {
    const target = (await api(page, 'GET',
      `/tasks?q=${SFX}-beta`)).body.items[0]
    await api(page, 'PATCH', `/tasks/${target.id}`, { status: 'archived' })

    await forceGrouped(page, 'status')
    await page.goto('/tasks')
    await listReady(page)
    await expect(page.getByTestId('task-group-archived')).toHaveCount(0)

    await page.getByTestId('task-filter-button').click()
    await page.getByTestId('task-archived-toggle').click()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('task-group-archived')).toHaveCount(1)

    await api(page, 'PATCH', `/tasks/${target.id}`, { status: 'idea' })
  })

test('grouping never contradicts an active filter on the same axis',
  async ({ page }) => {
    await forceGrouped(page, 'status')
    await page.goto('/tasks')
    await listReady(page)
    await expect(page.locator('section[data-testid^="task-group-"]'))
      .not.toHaveCount(1)

    await page.getByTestId('task-filter-button').click()
    await page.getByTestId('list-facet-status').click()
    await page.getByRole('option', { name: 'Ideas' }).click()
    await page.keyboard.press('Escape')
    // A chip reading "Ideas" above an Armed group full of rows is two
    // contradictory claims about scope on one screen.
    await expect(page.locator('section[data-testid^="task-group-"]'))
      .toHaveCount(1)
    await expect(page.getByTestId('task-group-idea')).toHaveCount(1)
  })

test('grouping by project puts the unfiled tasks in their own group',
  async ({ page }) => {
    await forceGrouped(page, 'project')
    await page.goto('/tasks')
    await listReady(page)
    await expect(page.getByTestId(`task-group-${seeded.project.id}`)).toBeVisible()
    await expect(page.getByTestId('task-group-none')).toBeVisible()
    await expect(page.getByTestId(`task-group-${seeded.project.id}`))
      .toContainText(`${SFX}-alpha`)
    await expect(page.getByTestId('task-group-none')).toContainText(`${SFX}-beta`)
  })

// ── bulk ─────────────────────────────────────────────────────

test('a bulk archive moves every selected row it is allowed to', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)

  for (const name of ['alpha', 'beta']) {
    await page.locator('[data-testid="task-row"]', { hasText: `${SFX}-${name}` })
      .first().getByTestId('task-select').click()
  }
  await expect(page.getByTestId('task-bulk')).toContainText('2 selected')
  await page.getByTestId('task-bulk-archive').click()

  await expect(page.locator('[data-testid="task-row"]',
    { hasText: `${SFX}-alpha` })).toHaveCount(0)
  const archived = await api(page, 'GET', '/tasks?status=archived')
  const titles = archived.body.items.map(t => t.title)
  expect(titles).toEqual(expect.arrayContaining([`${SFX}-alpha`, `${SFX}-beta`]))
})

test('archived rows are reachable by asking for them', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)
  await page.getByTestId('task-filter-button').click()
  await page.getByTestId('task-archived-toggle').click()
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-testid="task-row"]',
    { hasText: `${SFX}-alpha` })).toHaveCount(1)
})

// ── projects are repos ───────────────────────────────────────

test('the project picker offers the registered repos and nothing else',
  async ({ page }) => {
    await forceFlat(page)
    await page.goto('/tasks')
    await listReady(page)

    const repos = (await api(page, 'GET', '/repos')).body.repos
    await page.getByTestId('pipeline-project').click()
    for (const repo of repos) {
      await expect(page.getByRole('option', { name: repo.name, exact: true }))
        .toBeVisible()
    }
    // The backlog is a real option, not the absence of one.
    await expect(page.getByRole('option', { name: 'No project', exact: true }))
      .toBeVisible()
  })

test('a row filed under a repo names that repo', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)
  // `gamma`, not `alpha`: this file is serial and the bulk test above archives
  // alpha, which the default view then rightly hides.
  const row = page.locator('[data-testid="task-row"]',
    { hasText: `${SFX}-gamma` }).first()
  await expect(row.getByTestId('task-project')).toHaveText(seeded.project.name)
})

test('the edit sheet files a single task, and unfiles it again',
  async ({ page }) => {
    await forceFlat(page)
    await page.goto('/tasks')
    // Its own row: this file is serial, and an earlier test archives the
    // seeded ones out of the default view.
    await api(page, 'POST', '/tasks', { title: `${SFX}-filing` })
    await page.reload()
    await listReady(page)

    // The bulk bar can move a selection, but a single task needs a control on
    // its own sheet — selecting one row to reach a bulk action is not one.
    const row = page.locator('[data-testid="task-row"]',
      { hasText: `${SFX}-filing` }).first()
    await row.hover()
    await row.getByTestId('task-edit').click()
    await expect(page.getByTestId('task-edit-form')).toBeVisible()

    await page.getByTestId('task-edit-project').click()
    await page.getByRole('option', { name: seeded.project.name, exact: true })
      .click()
    await page.getByTestId('task-edit-save').click()
    await expect(page.getByTestId('task-edit-form')).toHaveCount(0)

    const filed = (await api(page, 'GET',
      `/tasks?q=${SFX}-filing`)).body.items[0]
    expect(filed.project_id).toBe(seeded.project.id)

    const back = page.locator('[data-testid="task-row"]',
      { hasText: `${SFX}-filing` }).first()
    await back.hover()
    await back.getByTestId('task-edit').click()
    await page.getByTestId('task-edit-project').click()
    await page.getByRole('option', { name: 'No project', exact: true }).click()
    await page.getByTestId('task-edit-save').click()
    await expect(page.getByTestId('task-edit-form')).toHaveCount(0)

    const unfiled = (await api(page, 'GET',
      `/tasks?q=${SFX}-filing`)).body.items[0]
    expect(unfiled.project_id).toBeNull()
  })

test('the edit sheet sets priority on a single task', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)

  const row = page.locator('[data-testid="task-row"]',
    { hasText: `${SFX}-filing` }).first()
  await row.hover()
  await row.getByTestId('task-edit').click()
  await page.getByTestId('task-edit-priority').click()
  await page.getByRole('option', { name: 'Urgent', exact: true }).click()
  await page.getByTestId('task-edit-save').click()
  await expect(page.getByTestId('task-edit-form')).toHaveCount(0)

  const saved = (await api(page, 'GET', `/tasks?q=${SFX}-filing`)).body.items[0]
  expect(saved.priority).toBe('urgent')
})

test('a task cannot be filed under a repo that is not registered',
  async ({ page }) => {
    // No foreign key does this — `pattern_deployments.project_id` has none
    // either — so the writer checks, and an unregistered id is refused rather
    // than stored as a chip nothing can resolve.
    const refused = await api(page, 'POST', '/tasks',
      { title: `${SFX}-nowhere`, project_id: 999999 })
    expect(refused.status).toBe(400)
    expect(refused.body.field).toBe('project_id')
  })

// ── keyboard ─────────────────────────────────────────────────

test('c opens the composer and / focuses the search', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)

  await page.keyboard.press('c')
  await expect(page.locator('#task-title')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('#task-title')).toHaveCount(0)

  await page.keyboard.press('/')
  await expect(page.getByTestId('list-search')).toBeFocused()
})

test('a chord typed into a field is text, not a command', async ({ page }) => {
  await forceFlat(page)
  await page.goto('/tasks')
  await listReady(page)
  await page.getByTestId('list-search').fill('')
  await page.getByTestId('list-search').press('c')
  await expect(page.locator('#task-title')).toHaveCount(0)
  await expect(page.getByTestId('list-search')).toHaveValue('c')
})
