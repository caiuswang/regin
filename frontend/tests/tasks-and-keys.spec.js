import { test, expect } from './auth-fixture.js'
import { rowAction, verbsFor } from './helpers/rowAction.js'
import { contentOverflow, settle } from './helpers/overflow.js'

// Serial: these share one `agent_tasks` table, and the 0-task assertion is
// only true before any of the others has captured anything.
test.describe.configure({ mode: 'serial' })

function watchConsole(page) {
  const errors = []
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
  page.on('pageerror', e => errors.push(String(e)))
  return errors
}

// Grouping and every filter axis persist in localStorage, so a run would
// otherwise inherit whatever the last one left selected. These tests count and
// address rows in the flat list, which is `group='none'`.
const FLAT_VIEW = {
  'regin.tasks.group': 'none',
  'regin.tasks.status': '',
  'regin.tasks.archived': '',
  'regin.tasks.priority': '',
  'regin.tasks.project': '',
  'regin.tasks.sort': 'created_desc',
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((state) => {
    for (const [key, value] of Object.entries(state)) {
      localStorage.setItem(key, value)
    }
  }, FLAT_VIEW)
})

// The task specs share one `agent_tasks` table and run in parallel workers, so
// a count over the whole feed counts the other file's captures too. Every
// capture here writes this attempt's marker into the task body and every test
// that counts or orders rows narrows the *server-side* search to it — a retry
// gets its own marker, so re-running this serial file never counts what the
// attempt before it left in the table.
function runMark() {
  const info = test.info()
  return `mark${info.workerIndex}x${info.retry}`
}

// Ungrouped: these tests act on the flat list's rows (archive, restore,
// select) rather than on the next-step list's one action per row.
async function gotoTasks(page) {
  await page.goto('/tasks?group=none')
  await page.getByTestId('list-search').fill(runMark())
  await page.getByTestId('list-search').press('Enter')
  // A reload keeps the previous rows on screen (dimmed) until it lands, so
  // wait for the searched read itself rather than for any rows at all.
  await expect(page).toHaveURL(new RegExp(`q=${runMark()}`))
  await expect(page.locator('[data-testid="task-list"][aria-busy="true"]')).toHaveCount(0)
}

async function openComposer(page) {
  await page.getByTestId('task-new').click()
  await expect(page.locator('#task-title')).toBeVisible()
}

// The status filter is a portalled listbox: the option is clicked in the
// portal at the end of <body>, not inside the filter bar.
async function selectStatus(page, label) {
  await page.getByTestId('task-filter-button').click()
  await page.getByTestId('list-facet-status').click()
  await page.getByRole('option', { name: label, exact: true }).click()
  // Close the menu so it cannot cover the rows the test acts on next.
  const menu = page.getByRole('dialog', { name: 'Filter' })
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0, { timeout: 1500 })
    .catch(() => page.getByTestId('task-filter-button').click())
  await expect(menu).toHaveCount(0)
}

// The stale note and the refusal are SIBLINGS of the row, not children: a row
// is one line, and what it has to say about itself goes under it.
function noteAfter(page, id, testId) {
  return page.locator(`#task-${id} + [data-testid="${testId}"]`)
}

async function capture(page, title) {
  await openComposer(page)
  await page.fill('#task-title', title)
  await page.fill('#task-body', runMark())
  await page.getByTestId('task-submit').click()
  await expect(page.getByTestId('task-list').getByText(title, { exact: true }))
    .toBeVisible()
}

test.describe('/tasks', () => {
  test('renders an empty state before anything is captured', async ({ page }) => {
    const errors = watchConsole(page)
    await gotoTasks(page)
    await settle(page)
    const rows = await page.locator('.task-row').count()
    test.skip(rows > 0, 'tasks already captured (retry, or an earlier test)')

    await expect(page.getByTestId('list-status')).toHaveAttribute('data-state', /^(empty|no-match)$/)
    expect(errors).toEqual([])
  })

  test('captures one idea, then N, newest first', async ({ page }) => {
    await gotoTasks(page)
    await capture(page, 'first captured idea')
    await expect(page.locator('.task-row')).toHaveCount(1)
    await expect(page.locator('.task-row').first())
      .toHaveAttribute('data-status', 'idea')
    await expect(page.locator('.task-row').first().getByRole('img', { name: 'idea' }))
      .toBeVisible()

    await capture(page, 'second captured idea')
    await capture(page, 'third captured idea')
    await expect(page.locator('.task-row')).toHaveCount(3)
    await expect(page.locator('.task-title').first())
      .toHaveText('third captured idea')
  })

  test('composer refuses an empty title and an over-long one', async ({ page }) => {
    await gotoTasks(page)
    await openComposer(page)
    await expect(page.getByTestId('task-submit')).toBeDisabled()

    // Marked like a real capture, so a title that slipped past the refusal
    // would land in the very list this counts.
    await page.fill('#task-body', runMark())
    await page.fill('#task-title', 'x'.repeat(201))
    await expect(page.getByTestId('task-submit')).toBeEnabled()
    await page.getByTestId('task-submit').click()
    await expect(page.getByTestId('task-form-error'))
      .toContainText('200 characters or fewer')
    // Refused client-side: nothing was sent, so nothing was added.
    await expect(page.locator('.task-row')).toHaveCount(3)
  })

  test('a server 400 is shown, not swallowed', async ({ page }) => {
    await page.route('**/api/tasks', async (route) => {
      if (route.request().method() !== 'POST') return route.continue()
      await route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'unknown field: cwd', field: 'cwd' }),
      })
    })
    await gotoTasks(page)
    await openComposer(page)
    await page.fill('#task-title', 'rejected by the server')
    await page.getByTestId('task-submit').click()
    await expect(page.getByTestId('task-form-error'))
      .toHaveText('unknown field: cwd')
  })

  test('archive and restore, with the status filter following', async ({ page }) => {
    await gotoTasks(page)
    const row = page.locator('.task-row').first()
    await rowAction(row, 'archive')

    // Archived is a world of its own: it is reached by asking for it by name,
    // so an archived task leaves the unfiltered feed.
    await expect(page.locator('.task-row')).toHaveCount(2)

    await selectStatus(page, 'Archived')
    await expect(page.locator('.task-row')).toHaveCount(1)
    await expect(page.locator('.task-row').first())
      .toHaveAttribute('data-status', 'archived')

    await rowAction(page.locator('.task-row').first(), 'restore')
    await expect(page.locator('.task-row')).toHaveCount(0)

    await selectStatus(page, 'Any status')
    await expect(page.locator('.task-row')).toHaveCount(3)
  })

  test('the SPA never calls the API-key capture endpoint', async ({ page }) => {
    const captureCalls = []
    page.on('request', r => {
      if (r.url().includes('/api/tasks/capture')) captureCalls.push(r.url())
    })
    await gotoTasks(page)
    await capture(page, 'browser routes only')
    expect(captureCalls).toEqual([])
  })
})

// ── slice 3: arm / run / stop / deps / DAG ───────────────────
//
// The refusal paths (a blocked run, a cycle) are driven against the REAL
// backend — they are the paths that must never launch anything. The success
// paths of run/stop/interrupt are served from intercepted routes on purpose:
// letting them through would spawn an actual agent process out of a test run.

const NOW = '2026-09-11 10:00:00'

function fakeTask(id, over = {}) {
  return {
    id, title: `mock task ${id}`, body: '', status: 'idea', source: 'web',
    external_id: null, tags: [], links: [], created_by: 1,
    created_at: NOW, updated_at: NOW, params: {}, verify_cmd: null,
    autorun: false, project_id: null, priority: 'none',
    ...verbsFor(over.status || 'idea'), ...over,
  }
}

// "Nothing here" is the server's count, never `items.length` — so a served
// feed has to answer for the counts too or its rows render as an empty
// backlog.
function fakeCounts(tasks) {
  const byStatus = {}
  for (const task of tasks) {
    byStatus[task.status] = (byStatus[task.status] || 0) + 1
  }
  return {
    by_status: byStatus,
    by_priority: { none: tasks.length },
    by_project: { none: tasks.length },
    total: tasks.length,
  }
}

async function mockCounts(page, tasksFor) {
  await page.route(/\/api\/tasks\/counts/, route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(fakeCounts(tasksFor())),
  }))
}

async function mockList(page, tasks) {
  await mockCounts(page, () => tasks)
  await page.route(/\/api\/tasks\?/, route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      items: tasks,
      pagination: { next_cursor: null, size: 50, has_next: false },
      status_filter: '',
      counts: fakeCounts(tasks),
    }),
  }))
}

async function mockRuns(page, runs) {
  await page.route(/\/api\/tasks\/\d+\/runs$/, route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ runs }),
  }))
}

// Grouped view: each group scopes its own feed by a query param, so a mock
// that answers every group with every task renders each row once per group.
async function mockGroupedList(page, tasks, held = {}) {
  await mockCounts(page, () => tasks)
  await page.route(/\/api\/tasks\?/, async route => {
    const status = new URL(route.request().url()).searchParams.get('status') || ''
    const items = status ? tasks.filter(t => t.status === status) : tasks
    if (held[status]) await held[status]
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items,
        pagination: { next_cursor: null, size: 50, has_next: false },
        status_filter: status,
        counts: fakeCounts(tasks),
      }),
    })
  })
}

async function groupByStatus(page) {
  await page.addInitScript(() => {
    localStorage.setItem('regin.tasks.group', 'status')
  })
}

async function rowIdFor(page, title) {
  const row = page.locator('.task-row', { hasText: title }).first()
  return Number((await row.getAttribute('id')).replace('task-', ''))
}

test.describe('/tasks — arming', () => {
  test('the arm sheet offers the served options and moves idea → armed', async ({ page }) => {
    const optionCalls = []
    const worktreeCalls = []
    page.on('request', r => {
      if (r.url().includes('/api/agent-runs/launch-options')) optionCalls.push(r.url())
      if (r.url().includes('/api/agent-runs/worktree-options')) worktreeCalls.push(r.url())
    })
    await gotoTasks(page)
    const row = page.locator('.task-row', { hasText: 'browser routes only' }).first()
    await rowAction(row, 'arm')
    await expect(page.getByTestId('task-arm-form')).toBeVisible()
    expect(optionCalls.length).toBeGreaterThan(0)
    // The worktree section asks its own, per-repo question.
    expect(worktreeCalls.length).toBeGreaterThan(0)

    // The menu is the install's, not a literal in the client: compare it to
    // what the endpoint itself answers.
    const served = await page.evaluate(async () => {
      const res = await fetch('/api/agent-runs/launch-options', {
        headers: { Authorization: `Bearer ${localStorage.getItem('regin_auth_token')}` },
      })
      return res.json()
    })
    expect(served.models.length).toBeGreaterThan(0)
    await page.getByTestId('task-arm-model').click()
    for (const model of served.models) {
      await expect(page.getByRole('option', { name: model, exact: true })).toBeVisible()
    }
    await page.getByRole('option', { name: served.models[0], exact: true }).click()

    await page.getByTestId('task-arm-verify').fill('echo verified')
    await page.getByTestId('task-arm-submit').click()

    const armed = page.locator('.task-row', { hasText: 'browser routes only' }).first()
    await expect(armed).toHaveAttribute('data-status', 'armed')

    // A row is a scan line and carries no execution parameters, so what the
    // task was armed WITH is read on the task's own page.
    await armed.getByTestId('task-id').click()
    await expect(page.locator('.td-params')).toContainText(served.models[0])
    await expect(page.locator('.td-params')).toContainText('echo verified')
  })

  test('an arm 400 names the rejected field', async ({ page }) => {
    await page.route(/\/api\/tasks\/\d+\/arm$/, route => route.fulfill({
      status: 400,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'unknown field: bogus', field: 'bogus' }),
    }))
    await gotoTasks(page)
    await rowAction(page.locator('.task-row').first(), 'arm')
    await page.getByTestId('task-arm-submit').click()
    const refusal = page.getByTestId('task-arm-refusal')
    await expect(refusal).toContainText('unknown field: bogus')
    await expect(refusal).toContainText('rejected field: bogus')
  })

  // The served list is whatever the scratch install deploys, which may be
  // nothing; the tests care about the picker, so they pin what it is offered.
  const SERVED_SKILLS = ['feature-pipeline', 'goal-verified']
  async function serveSkills(page) {
    await page.route(/\/api\/skills(\?|$)/, route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ rows: SERVED_SKILLS.map(id => ({ id })), total: SERVED_SKILLS.length }),
    }))
  }

  test('the skill field is the shared listbox, offering the served skills', async ({ page }) => {
    await serveSkills(page)
    await gotoTasks(page)
    await rowAction(page.locator('.task-row').first(), 'arm')
    const form = page.getByTestId('task-arm-form')
    await expect(form).toBeVisible()
    // The native suggestion popup is gone: nothing in the sheet is a datalist.
    await expect(form.locator('datalist')).toHaveCount(0)

    const trigger = page.getByTestId('task-arm-skill')
    await expect(trigger).toContainText('none')
    await trigger.click()
    await expect(page.getByRole('option', { name: /^none/ })).toBeVisible()
    for (const id of SERVED_SKILLS) {
      await expect(page.getByRole('option', { name: id, exact: true })).toBeVisible()
    }
    await expect(page.getByRole('option', { name: 'custom…', exact: true })).toBeVisible()
    await page.keyboard.press('Escape')
  })

  test('an armed skill is sent as {name, args} and shown on the row', async ({ page }) => {
    await serveSkills(page)
    await gotoTasks(page)
    await capture(page, 'run as a skill')
    const row = page.locator('.task-row', { hasText: 'run as a skill' }).first()
    await rowAction(row, 'arm')
    await expect(page.getByTestId('task-arm-form')).toBeVisible()

    // The args field only appears once a skill is named: with no skill there
    // is nothing for it to be arguments to.
    await expect(page.getByTestId('task-arm-skill-args')).toHaveCount(0)
    await page.getByTestId('task-arm-skill').click()
    await page.getByRole('option', { name: 'goal-verified', exact: true }).click()
    await page.getByTestId('task-arm-skill-args').fill('--strict')

    const [armRequest] = await Promise.all([
      page.waitForRequest(r => /\/api\/tasks\/\d+\/arm$/.test(r.url())),
      page.getByTestId('task-arm-submit').click(),
    ])
    expect(armRequest.postDataJSON().skill).toEqual({ name: 'goal-verified', args: '--strict' })
    await expect(row).toHaveAttribute('data-status', 'armed')

    // The skill is an execution parameter, so the task's own page is where it
    // is printed.
    await row.getByTestId('task-id').click()
    const params = page.locator('.td-params')
    await expect(params).toContainText('"name": "goal-verified"')
    await expect(params).toContainText('"args": "--strict"')

    // Re-opening the sheet seeds the draft from what was armed.
    await gotoTasks(page)
    await selectStatus(page, 'Armed')
    const armed = page.locator('.task-row', { hasText: 'run as a skill' }).first()
    await rowAction(armed, 'arm')
    await expect(page.getByTestId('task-arm-skill')).toContainText('goal-verified')
    await expect(page.getByTestId('task-arm-skill-args')).toHaveValue('--strict')
  })

  test('a skill regin does not deploy is still armable via custom…', async ({ page }) => {
    await serveSkills(page)
    await gotoTasks(page)
    await capture(page, 'run a local skill')
    const row = page.locator('.task-row', { hasText: 'run a local skill' }).first()
    await rowAction(row, 'arm')
    await expect(page.getByTestId('task-arm-form')).toBeVisible()

    await expect(page.getByTestId('task-arm-skill-custom')).toHaveCount(0)
    await page.getByTestId('task-arm-skill').click()
    await page.getByRole('option', { name: 'custom…', exact: true }).click()
    // The slash is accepted here and stripped by the server, as before.
    await page.getByTestId('task-arm-skill-custom').fill('/my-local-skill')

    const [armRequest] = await Promise.all([
      page.waitForRequest(r => /\/api\/tasks\/\d+\/arm$/.test(r.url())),
      page.getByTestId('task-arm-submit').click(),
    ])
    expect(armRequest.postDataJSON().skill).toEqual({ name: '/my-local-skill', args: '' })
    await expect(row).toHaveAttribute('data-status', 'armed')

    // An unlisted name is adopted into the custom slot on re-open, not reset
    // to "none".
    await gotoTasks(page)
    await selectStatus(page, 'Armed')
    const armed = page.locator('.task-row', { hasText: 'run a local skill' }).first()
    await rowAction(armed, 'arm')
    await expect(page.getByTestId('task-arm-skill')).toContainText('custom…')
    await expect(page.getByTestId('task-arm-skill-custom')).toHaveValue('my-local-skill')
  })

  // Arming many tasks the same way is the common case: the sheet remembers the
  // last accepted pick and offers it to the NEXT never-armed task.
  test('a never-armed task is filled from the last arm, visibly and undoably', async ({ page }) => {
    await serveSkills(page)
    await gotoTasks(page)
    await capture(page, 'first quick arm')
    await capture(page, 'second quick arm')

    const served = await page.evaluate(async () => {
      const res = await fetch('/api/agent-runs/launch-options', {
        headers: { Authorization: `Bearer ${localStorage.getItem('regin_auth_token')}` },
      })
      return res.json()
    })

    const first = page.locator('.task-row', { hasText: 'first quick arm' }).first()
    await rowAction(first, 'arm')
    await expect(page.getByTestId('task-arm-form')).toBeVisible()
    // Nothing has been armed in this browser yet, so there is nothing to recall.
    await expect(page.getByTestId('task-arm-recalled')).toHaveCount(0)

    await page.getByTestId('task-arm-model').click()
    await page.getByRole('option', { name: served.models[0], exact: true }).click()
    await page.getByTestId('task-arm-skill').click()
    await page.getByRole('option', { name: 'goal-verified', exact: true }).click()
    await page.getByTestId('task-arm-skill-args').fill('--strict')
    await page.getByTestId('task-arm-verify').fill('echo remembered')
    await page.getByTestId('task-arm-submit').click()
    await expect(first).toHaveAttribute('data-status', 'armed')

    // What is remembered is exactly the how-to-run fields. A worktree branch is
    // named for ONE task, so carrying it would aim two tasks at one checkout.
    const stored = await page.evaluate(
      () => JSON.parse(localStorage.getItem('regin.tasks.arm.last')))
    expect(Object.keys(stored).sort()).toEqual([
      'cwd', 'effort', 'model', 'one_shot', 'permission_mode',
      'skill', 'skillArgs', 'verify_cmd',
    ])

    // Re-opening the task that WAS armed reads its own attempt, and must not
    // claim to have been filled from somewhere else.
    await rowAction(first, 'arm')
    await expect(page.getByTestId('task-arm-form')).toBeVisible()
    await expect(page.getByTestId('task-arm-recalled')).toHaveCount(0)
    await page.getByRole('button', { name: 'Cancel' }).click()

    const second = page.locator('.task-row', { hasText: 'second quick arm' }).first()
    await rowAction(second, 'arm')
    await expect(page.getByTestId('task-arm-recalled')).toBeVisible()
    await expect(page.getByTestId('task-arm-verify')).toHaveValue('echo remembered')
    await expect(page.getByTestId('task-arm-model')).toContainText(served.models[0])
    await expect(page.getByTestId('task-arm-skill')).toContainText('goal-verified')
    await expect(page.getByTestId('task-arm-skill-args')).toHaveValue('--strict')

    // Reset is the escape hatch: it blanks the form AND forgets the pick, so
    // the next sheet opens clean rather than re-offering it.
    await page.getByTestId('task-arm-reset').click()
    await expect(page.getByTestId('task-arm-recalled')).toHaveCount(0)
    await expect(page.getByTestId('task-arm-verify')).toHaveValue('')
    await expect(page.getByTestId('task-arm-skill-args')).toHaveCount(0)
    expect(await page.evaluate(
      () => localStorage.getItem('regin.tasks.arm.last'))).toBeNull()
  })

  // `custom…` names a slot in the form, not a model. Remembering the sentinel
  // instead of what was typed under it would arm the next task with the literal
  // string `__custom__` — which the arm route accepts as a free-form model and
  // only the launch would reject.
  test('a recalled custom model and skill carry the real ids, not the sentinel', async ({ page }) => {
    await serveSkills(page)
    await gotoTasks(page)
    await capture(page, 'first custom arm')
    await capture(page, 'second custom arm')

    const first = page.locator('.task-row', { hasText: 'first custom arm' }).first()
    await rowAction(first, 'arm')
    await expect(page.getByTestId('task-arm-form')).toBeVisible()
    await page.getByTestId('task-arm-model').click()
    await page.getByRole('option', { name: 'custom…', exact: true }).click()
    await page.getByTestId('task-arm-model-custom').fill('claude-opus-4-8')
    await page.getByTestId('task-arm-skill').click()
    await page.getByRole('option', { name: 'custom…', exact: true }).click()
    await page.getByTestId('task-arm-skill-custom').fill('my-local-skill')
    await page.getByTestId('task-arm-submit').click()
    await expect(first).toHaveAttribute('data-status', 'armed')

    const stored = await page.evaluate(
      () => JSON.parse(localStorage.getItem('regin.tasks.arm.last')))
    expect(stored.model).toBe('claude-opus-4-8')
    expect(stored.skill).toBe('my-local-skill')

    const second = page.locator('.task-row', { hasText: 'second custom arm' }).first()
    await rowAction(second, 'arm')
    await expect(page.getByTestId('task-arm-recalled')).toBeVisible()
    await expect(page.getByTestId('task-arm-model')).toContainText('custom…')
    await expect(page.getByTestId('task-arm-model-custom')).toHaveValue('claude-opus-4-8')
    await expect(page.getByTestId('task-arm-skill')).toContainText('custom…')
    await expect(page.getByTestId('task-arm-skill-custom')).toHaveValue('my-local-skill')

    const [armRequest] = await Promise.all([
      page.waitForRequest(r => /\/api\/tasks\/\d+\/arm$/.test(r.url())),
      page.getByTestId('task-arm-submit').click(),
    ])
    const body = armRequest.postDataJSON()
    expect(body.model).toBe('claude-opus-4-8')
    expect(body.skill).toEqual({ name: 'my-local-skill', args: '' })
  })

  // Args are hidden the moment the skill picker goes back to "none", and a
  // sentinel left in an older store names a slot rather than a model. Neither
  // may become the sole reason the next sheet claims a carry-over.
  test('an invisible field never carries, and a stored sentinel is refused', async ({ page }) => {
    await gotoTasks(page)
    await capture(page, 'sentinel preset target')
    await page.evaluate(() => localStorage.setItem('regin.tasks.arm.last', JSON.stringify({
      cwd: '', model: '__custom__', permission_mode: '', effort: '',
      skill: '', skillArgs: '--strict', verify_cmd: '', one_shot: false,
    })))
    const row = page.locator('.task-row', { hasText: 'sentinel preset target' }).first()
    await rowAction(row, 'arm')
    await expect(page.getByTestId('task-arm-form')).toBeVisible()
    await expect(page.getByTestId('task-arm-model-custom')).toHaveCount(0)
    await expect(page.getByTestId('task-arm-skill-args')).toHaveCount(0)
    await expect(page.getByTestId('task-arm-recalled')).toHaveCount(0)
    await page.getByRole('button', { name: 'Cancel' }).click()

    // Nor may they be written: args typed under a skill that is then cleared
    // are not part of the selection.
    const again = page.locator('.task-row', { hasText: 'sentinel preset target' }).first()
    await rowAction(again, 'arm')
    await page.getByTestId('task-arm-skill').click()
    await page.getByRole('option', { name: 'custom…', exact: true }).click()
    await page.getByTestId('task-arm-skill-custom').fill('throwaway')
    await page.getByTestId('task-arm-skill-args').fill('--strict')
    await page.getByTestId('task-arm-skill').click()
    await page.getByRole('option', { name: /^none/ }).click()
    await page.getByTestId('task-arm-submit').click()
    await expect(again).toHaveAttribute('data-status', 'armed')
    const stored = await page.evaluate(
      () => JSON.parse(localStorage.getItem('regin.tasks.arm.last')))
    expect(stored.skill).toBe('')
    expect(stored.skillArgs).toBe('')
  })

  // A stored pick the install no longer offers is dropped — and with nothing
  // left to carry, the banner must not claim a carry-over over a blank form.
  test('a preset whose every value went stale drops the banner too', async ({ page }) => {
    await gotoTasks(page)
    await capture(page, 'stale preset target')
    await page.evaluate(() => localStorage.setItem('regin.tasks.arm.last', JSON.stringify({
      cwd: '/gone/from/this/install',
      model: '', permission_mode: '', effort: '',
      skill: '', skillArgs: '', verify_cmd: '', one_shot: false,
    })))
    const row = page.locator('.task-row', { hasText: 'stale preset target' }).first()
    await rowAction(row, 'arm')
    await expect(page.getByTestId('task-arm-form')).toBeVisible()
    await expect(page.getByTestId('task-arm-cwd')).toContainText('server working directory')
    await expect(page.getByTestId('task-arm-recalled')).toHaveCount(0)
  })

})

test.describe('/tasks — dependencies and refusals', () => {
  test('a saved dep set blocks the run, and the 409 links its blockers', async ({ page }) => {
    await gotoTasks(page)
    await capture(page, 'the dependent task')
    const blockerId = await rowIdFor(page, 'first captured idea')
    await rowIdFor(page, 'the dependent task')

    const dependent = page.locator('.task-row', { hasText: 'the dependent task' }).first()
    await rowAction(dependent, 'deps')
    await page.getByTestId(`task-dep-${blockerId}`).click()
    await page.getByTestId('task-deps-save').click()
    await expect(page.getByTestId('task-deps-list')).toHaveCount(0)

    // Arm it for real, then ask it to run: the dependency is not done, so the
    // server refuses with 409 and names what is in the way.
    const armedRow = page.locator('.task-row', { hasText: 'the dependent task' }).first()
    await rowAction(armedRow, 'arm')
    await page.getByTestId('task-arm-submit').click()

    // Armed but blocked: the row offers no Run, only the link to what it waits
    // on — the same primary the board card and the graph node draw.
    const blocked = page.locator('.task-row', { hasText: 'the dependent task' }).first()
    await expect(blocked.getByTestId('task-action-run')).toHaveCount(0)
    const link = blocked.getByTestId('task-action-waits')
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute('href', `/tasks/${blockerId}`)
  })

  test('a cycle is refused, naming the pair', async ({ page }) => {
    await gotoTasks(page)
    await selectStatus(page, 'Any status')
    const dependentId = await rowIdFor(page, 'the dependent task')

    // The reverse edge would close the loop the previous test opened.
    const blocker = page.locator('.task-row', { hasText: 'first captured idea' }).first()
    const blockerId = await rowIdFor(page, 'first captured idea')
    await rowAction(blocker, 'deps')
    await page.getByTestId(`task-dep-${dependentId}`).click()
    await page.getByTestId('task-deps-save').click()

    const refusal = page.getByTestId('task-deps-refusal')
    await expect(refusal).toContainText('cycle')
    await expect(refusal).toContainText(`${blockerId} → ${dependentId}`)
  })
})

test.describe('/tasks — a run and its liveness', () => {
  test('a live run offers Stop and Interrupt, and calls those routes', async ({ page }) => {
    const posted = []
    await mockList(page, [fakeTask(901, { title: 'a live run', status: 'running' })])
    await mockRuns(page, [{
      id: 5, task_id: 901, trace_id: 'sdk-live-1', attempt: 1, status: 'running',
      started_at: NOW, ended_at: null, exit_reason: null, verify_exit_code: null,
      agent_run: { status: 'running', pid: 4242 }, live: true,
    }])
    await page.route(/\/api\/tasks\/\d+\/(stop|interrupt)$/, route => {
      posted.push(new URL(route.request().url()).pathname)
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ delivered: true, detail: 'ok', trace_id: 'sdk-live-1', run_id: 5 }),
      })
    })

    await page.goto('/tasks')
    const row = page.locator('.task-row', { hasText: 'a live run' }).first()
    await expect(row.getByTestId('task-live')).toBeVisible()
    await expect(noteAfter(page, 901, 'task-stale')).toHaveCount(0)

    await expect(row.getByTestId('task-action-watch')).toBeVisible()
    await rowAction(row, 'interrupt')
    await expect.poll(() => posted.length).toBe(1)
    await row.getByTestId('task-action-stop').click()
    await page.getByRole('button', { name: 'Stop run', exact: true }).click()
    await expect.poll(() => posted.length).toBe(2)
    expect(posted).toEqual(['/api/tasks/901/interrupt', '/api/tasks/901/stop'])
  })

  test('Run posts the run route and the row follows into running', async ({ page }) => {
    // Served, not launched: letting this through would start a real agent.
    let status = 'armed'
    const served = () => [fakeTask(905, { title: 'about to run', status })]
    await mockCounts(page, served)
    await page.route(/\/api\/tasks\?/, route => route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: served(),
        pagination: { next_cursor: null, size: 50, has_next: false },
        status_filter: '',
        counts: fakeCounts(served()),
      }),
    }))
    await mockRuns(page, [{
      id: 9, task_id: 905, trace_id: 'sdk-fresh-1', attempt: 1, status: 'running',
      started_at: NOW, ended_at: null, exit_reason: null, verify_exit_code: null,
      agent_run: { status: 'running', pid: 4243 }, live: true,
    }])
    const posted = []
    await page.route(/\/api\/tasks\/\d+\/run$/, route => {
      posted.push(new URL(route.request().url()).pathname)
      status = 'running'
      return route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ run: { id: 9, task_id: 905, attempt: 1, status: 'running' } }),
      })
    })

    await page.goto('/tasks')
    const row = page.locator('.task-row', { hasText: 'about to run' }).first()
    await row.getByTestId('task-action-run').click()
    await expect.poll(() => posted).toEqual(['/api/tasks/905/run'])

    const after = page.locator('.task-row', { hasText: 'about to run' }).first()
    await expect(after).toHaveAttribute('data-status', 'running')
    await expect(after.getByTestId('task-live')).toBeVisible()
    await expect(after.getByTestId('task-action-watch')).toBeVisible()
    await expect(after.getByTestId('task-action-stop')).toBeVisible()
    await after.getByTestId('task-menu').click()
    await expect(page.getByRole('menu').getByTestId('task-verb-interrupt')).toBeVisible()
  })

  test('a running row with nothing behind it reads distinctly', async ({ page }) => {
    await mockList(page, [fakeTask(902, { title: 'a dead run', status: 'running' })])
    await mockRuns(page, [{
      id: 6, task_id: 902, trace_id: 'sdk-dead-1', attempt: 2, status: 'running',
      started_at: NOW, ended_at: null, exit_reason: null, verify_exit_code: null,
      agent_run: { status: 'running', pid: 1 }, live: false,
    }])

    await page.goto('/tasks')
    const row = page.locator('.task-row', { hasText: 'a dead run' }).first()
    await expect(row.getByTestId('task-not-live')).toBeVisible()
    await expect(noteAfter(page, 902, 'task-stale')).toBeVisible()
    await expect(row.getByTestId('task-live')).toHaveCount(0)
    // The way out of a stranded row is another attempt, so Run stays offered.
    await expect(row.getByTestId('task-action-run')).toHaveText(/Run again/)
    await expect(row.getByTestId('task-action-stop')).toHaveCount(0)
  })

  test('a group holding no running rows does not strand another group', async ({ page }) => {
    await groupByStatus(page)
    // The idea group answers LAST — every group reports what it loaded, and a
    // group holding no running row has no liveness to report.
    let release = () => {}
    const idea = new Promise(resolve => { release = resolve })
    await mockGroupedList(page, [
      fakeTask(906, { title: 'a grouped live run', status: 'running' }),
      fakeTask(907, { title: 'an idea beside it', status: 'idea' }),
    ], { idea })
    await mockRuns(page, [{
      id: 10, task_id: 906, trace_id: 'sdk-grouped-1', attempt: 1, status: 'running',
      started_at: NOW, ended_at: null, exit_reason: null, verify_exit_code: null,
      agent_run: { status: 'running', pid: 4244 }, live: true,
    }])

    await page.goto('/tasks')
    const row = page.locator('.task-row', { hasText: 'a grouped live run' }).first()
    await expect(row.getByTestId('task-live')).toBeVisible()
    await expect(row.getByTestId('task-action-stop')).toBeVisible()

    release()
    await expect(page.locator('.task-row', { hasText: 'an idea beside it' })).toBeVisible()
    await settle(page)
    await expect(row.getByTestId('task-live')).toBeVisible()
    await expect(noteAfter(page, 906, 'task-stale')).toHaveCount(0)
    await expect(row.getByTestId('task-not-live')).toHaveCount(0)
  })

  test('a running row says nothing about liveness until it has an answer', async ({ page }) => {
    let release = () => {}
    const held = new Promise(resolve => { release = resolve })
    await mockList(page, [fakeTask(908, { title: 'an unanswered run', status: 'running' })])
    await page.route(/\/api\/tasks\/\d+\/runs$/, async route => {
      await held
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ runs: [{
          id: 11, task_id: 908, trace_id: 'sdk-slow-1', attempt: 1, status: 'running',
          started_at: NOW, ended_at: null, exit_reason: null, verify_exit_code: null,
          agent_run: { status: 'running', pid: 4245 }, live: true,
        }] }),
      })
    })

    await page.goto('/tasks')
    const row = page.locator('.task-row', { hasText: 'an unanswered run' }).first()
    await expect(row).toBeVisible()
    // Not knowing is not evidence the process is gone: no badge, no warning,
    // and no "Run again" that would launch a second agent over a live one.
    await expect(row.getByTestId('task-not-live')).toHaveCount(0)
    await expect(noteAfter(page, 908, 'task-stale')).toHaveCount(0)
    await expect(row.getByTestId('task-action-run')).toHaveCount(0)

    release()
    await expect(row.getByTestId('task-live')).toBeVisible()
  })

  test('run history lists attempts, each linking to its trace', async ({ page }) => {
    await mockList(page, [fakeTask(903, { title: 'a task with history', status: 'failed', run_count: 2 })])
    await mockRuns(page, [
      {
        id: 8, task_id: 903, trace_id: 'sdk-attempt-2', attempt: 2, status: 'failed',
        started_at: NOW, ended_at: NOW, exit_reason: 'the verify command failed',
        verify_exit_code: 1, agent_run: { status: 'exited', pid: 0 }, live: false,
      },
      {
        id: 7, task_id: 903, trace_id: 'sdk-attempt-1', attempt: 1, status: 'done',
        started_at: NOW, ended_at: NOW, exit_reason: null, verify_exit_code: 0,
        agent_run: { status: 'exited', pid: 0 }, live: false,
      },
    ])

    await page.goto('/tasks')
    await page.locator('.task-row', { hasText: 'a task with history' }).first()
      .getByTestId('task-run-count').click()
    await expect(page.getByTestId('task-runs').locator('.task-run')).toHaveCount(2)
    await expect(page.getByTestId('task-run-trace-2'))
      .toHaveAttribute('href', '/trace/sessions/sdk-attempt-2')
    await expect(page.getByTestId('task-run-trace-1'))
      .toHaveAttribute('href', '/trace/sessions/sdk-attempt-1')
    await expect(page.getByTestId('task-runs')).toContainText('verify exit 1')
  })

  test('a task never run offers no attempts chip', async ({ page }) => {
    await mockList(page, [fakeTask(904, { title: 'never run', status: 'armed' })])
    await mockRuns(page, [])
    await page.goto('/tasks')
    const row = page.locator('.task-row', { hasText: 'never run' }).first()
    await expect(row.getByTestId('task-action-run')).toBeVisible()
    await expect(row.getByTestId('task-run-count')).toHaveCount(0)
  })
})

async function mockGraph(page, nodes, edges) {
  // The panel asks with a query string (its node cap, and any focus), so the
  // pattern cannot end at the path.
  await page.route(/\/api\/tasks\/graph(\?|$)/, route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ nodes, edges }),
  }))
}

function graphNode(id, over = {}) {
  return {
    id, title: `node ${id}`, status: 'idea', autorun: false,
    blocked: false, blocked_by: [], runnable: false, ...over,
  }
}

test.describe('/tasks — the dependency picker', () => {
  async function openPicker(page, tasks, nodes, edges) {
    await mockList(page, tasks)
    await mockGraph(page, nodes, edges)
    await page.goto('/tasks')
    await rowAction(page.locator('.task-row', { hasText: tasks[0].title }).first(), 'deps')
    await expect(page.getByTestId('task-deps-list')).toBeVisible()
  }

  const self = fakeTask(900, { title: 'the picker task' })
  const nodes = [
    graphNode(900, { title: 'the picker task' }),
    graphNode(901, { title: 'still an idea' }),
    graphNode(902, { title: 'armed and waiting', status: 'armed' }),
    graphNode(903, { title: 'already done', status: 'done' }),
    graphNode(904, { title: 'put away', status: 'archived' }),
    graphNode(905, { title: 'fell over', status: 'failed' }),
  ]

  test('done and archived tasks are not offered as blockers', async ({ page }) => {
    await openPicker(page, [self], nodes, [])
    for (const id of [901, 902, 905]) {
      await expect(page.getByTestId(`task-dep-${id}`)).toBeVisible()
    }
    for (const id of [900, 903, 904]) {
      await expect(page.getByTestId(`task-dep-${id}`)).toHaveCount(0)
    }
  })

  test('a done task already ticked stays listed so it can be unticked', async ({ page }) => {
    await openPicker(page, [self], nodes, [{ task_id: 900, depends_on_id: 903 }])
    const ticked = page.getByTestId('task-dep-903')
    await expect(ticked).toBeVisible()
    await expect(ticked.locator('input')).toBeChecked()
    await expect(page.getByTestId('task-dep-904')).toHaveCount(0)
  })

  test('the filter narrows candidates by id or title, and keeps ticked rows', async ({ page }) => {
    await openPicker(page, [self], nodes, [{ task_id: 900, depends_on_id: 905 }])
    const filter = page.getByTestId('task-deps-filter')
    await filter.fill('armed')
    await expect(page.getByTestId('task-dep-902')).toBeVisible()
    await expect(page.getByTestId('task-dep-901')).toHaveCount(0)
    // The ticked one survives a filter it does not match.
    await expect(page.getByTestId('task-dep-905')).toBeVisible()

    await filter.fill('901')
    await expect(page.getByTestId('task-dep-901')).toBeVisible()
    await expect(page.getByTestId('task-dep-902')).toHaveCount(0)
  })

  test('an unmatched filter says so', async ({ page }) => {
    await openPicker(page, [self], nodes, [])
    await page.getByTestId('task-deps-filter').fill('nothing matches this')
    await expect(page.getByTestId('task-deps-no-match')).toBeVisible()
    await expect(page.locator('[data-testid^="task-dep-"]')).toHaveCount(0)
  })
})

const KEYS_SECTION = '/settings?section=api-keys'

test.describe('API keys (Settings section)', () => {
  test('mints a key, shows the plaintext once, then revokes it', async ({ page }) => {
    const errors = watchConsole(page)
    await page.goto(KEYS_SECTION)
    await page.fill('#key-name', 'e2e key')
    await page.getByTestId('mint-key').click()

    const minted = page.getByTestId('minted-key')
    await expect(minted).toBeVisible()
    await expect(minted).toContainText('only time you will see this key')
    const shown = await minted.locator('.minted-value').innerText()
    expect(shown).toMatch(/^rgn_[0-9a-f]{32}$/)
    await expect(page.getByTestId('copy-key')).toBeVisible()
    await page.getByTestId('copy-key').click()
    await expect(page.getByTestId('copy-key')).toContainText('Copied')

    // Reload: the plaintext is gone for good — only the prefix survives.
    await page.reload()
    await expect(page.getByTestId('minted-key')).toHaveCount(0)
    const table = page.getByTestId('keys-table')
    await expect(table).toContainText(shown.slice(0, 8))
    await expect(table).not.toContainText(shown)

    await page.getByRole('button', { name: 'Revoke e2e key' }).click()
    // ConfirmDialog labels its accept button with the dialog title.
    await page.getByRole('button', { name: 'Revoke API key' }).click()
    await expect(table.getByText('revoked')).toBeVisible()
    expect(errors).toEqual([])
  })

  test('the legacy /api-keys URL redirects into the settings section', async ({ page }) => {
    await page.goto('/api-keys')
    await expect(page).toHaveURL(/\/settings\?section=api-keys$/)
    await expect(page.getByTestId('mint-key')).toBeVisible()
    // The rail carries no query, so Settings is what must stay lit here.
    const item = page.locator('nav.sb-nav .sb-item', { hasText: 'Settings' })
    await expect(item).toHaveClass(/is-active/)
    await expect(page.locator('nav.sb-nav .sb-item', { hasText: 'API keys' }))
      .toHaveCount(0)
  })

  test('a non-admin sees no settings entry and no key management', async ({ page }) => {
    await page.addInitScript(() => {
      const raw = localStorage.getItem('regin_auth_user')
      if (raw) {
        const user = JSON.parse(raw)
        user.role = 'viewer'
        localStorage.setItem('regin_auth_user', JSON.stringify(user))
      }
    })
    await page.goto('/tasks')
    await expect(page.locator('nav.sb-nav .sb-item', { hasText: 'API keys' }))
      .toHaveCount(0)

    await page.goto(KEYS_SECTION)
    await expect(page.getByTestId('settings-nav-api-keys')).toHaveCount(0)
    await expect(page.getByTestId('keys-forbidden')).toBeVisible()
    await expect(page.getByTestId('mint-key')).toHaveCount(0)
  })
})

test.describe('sidebar + rendering', () => {
  for (const [path, label] of [['/overview', 'Pipeline'], ['/settings', 'Settings']]) {
    test(`${path} highlights its sidebar entry`, async ({ page }) => {
      await page.goto('/')
      const item = page.locator('nav.sb-nav .sb-item', { hasText: label })
      await item.click()

      // The LINK carries no query — AppLayout compares `route.path` only, so a
      // `?query` in the href would never highlight and never re-navigate. What
      // the page writes into the URL *after* arriving (a restored filter) is
      // the list's business and must not break the highlight, which is the
      // claim the two assertions below make.
      await expect(page).toHaveURL(new RegExp(`${path}(\\?|$)`))
      await expect(item).toHaveClass(/is-active/)
      await expect(item).toHaveAttribute('aria-current', 'page')
    })
  }

  for (const path of ['/tasks', KEYS_SECTION]) {
    for (const theme of ['light', 'dark']) {
      for (const width of [1440, 390]) {
        test(`${path} renders clean — ${theme} @ ${width}px`, async ({ page }) => {
          await page.setViewportSize({ width, height: 900 })
          await page.addInitScript(
            (t) => localStorage.setItem('regin_theme', t), theme)
          const errors = watchConsole(page)

          await page.goto(path)
          await settle(page)

          await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
          await expect(page.locator('.page-title')).toBeVisible()
          const m = await contentOverflow(page)
          expect(
            m.scrollWidth,
            `${path} @${width} overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ') || 'unknown'}`
          ).toBeLessThanOrEqual(m.clientWidth + 1)
          expect(errors, errors.join(' | ')).toEqual([])
        })
      }
    }
  }

  // The graph is the one panel with an intrinsic width of its own, so it gets
  // its own measurement: the drawing scrolls inside its wrapper rather than
  // pushing the page sideways. Below 768px the tab offers the list only.
  for (const theme of ['light', 'dark']) {
    for (const width of [1440, 1024]) {
      test(`/tasks graph renders clean — ${theme} @ ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 })
        await page.addInitScript((t) => localStorage.setItem('regin_theme', t), theme)
        const errors = watchConsole(page)

        await page.goto('/tasks')
        await page.getByRole('tab', { name: 'Graph' }).click()
        await settle(page)

        await expect(page.getByTestId('task-lane-graph')).toBeVisible()
        const m = await contentOverflow(page)
        expect(
          m.scrollWidth,
          `/tasks graph @${width} overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ') || 'unknown'}`
        ).toBeLessThanOrEqual(m.clientWidth + 1)
        expect(errors, errors.join(' | ')).toEqual([])
      })
    }
  }
})

// ── images on a captured idea ────────────────────────────────
//
// In THIS file, not its own, because the specs share one `agent_tasks` table:
// a second file capturing rows in parallel breaks the row-count assertions
// above. Appended last so everything before it still sees the counts it wrote.

// A 1×1 PNG — real magic bytes, because the server sniffs them rather than
// trusting any declared content type.
const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

function pngBuffer() {
  return Buffer.from(PNG_B64, 'base64')
}

async function attachViaPicker(scope) {
  await scope.getByTestId('task-image-file').setInputFiles({
    name: 'shot.png', mimeType: 'image/png', buffer: pngBuffer(),
  })
}

// Paste is the gesture this feature exists for, and no Playwright API fires
// one with a file on it — so the ClipboardEvent is built in the page, with the
// same `items` shape a real screenshot paste carries.
async function pasteInto(page, selector, b64 = PNG_B64) {
  await page.locator(selector).focus()
  await page.evaluate(([sel, data]) => {
    const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0))
    const file = new File([bytes], 'pasted.png', { type: 'image/png' })
    const dt = new DataTransfer()
    dt.items.add(file)
    document.querySelector(sel).dispatchEvent(
      new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, [selector, b64])
}

test.describe('/tasks images', () => {
  test('an image pasted into the composer lands on the captured task',
    async ({ page }) => {
      await gotoTasks(page)
      await openComposer(page)
      await pasteInto(page, '#task-title')

      // Held locally — the row does not exist yet, so nothing was uploaded.
      await expect(page.getByTestId('task-image-strip').locator('img'))
        .toHaveCount(1)

      await page.fill('#task-title', 'idea with a pasted screenshot')
      await page.fill('#task-body', runMark())
      await page.getByTestId('task-submit').click()
      await expect(page.getByTestId('task-list')
        .getByText('idea with a pasted screenshot', { exact: true })).toBeVisible()

      const row = page.locator('.task-row').first()
      await expect(row.getByTestId('task-image-count')).toHaveText('1 image')
      // The composer is clean again for the next idea.
      await openComposer(page)
      await expect(page.locator('#task-title')).toHaveValue('')
      await expect(page.getByTestId('task-image-strip').locator('img'))
        .toHaveCount(0)
    })

  test('a saved task reopens with its image, and can gain and lose one',
    async ({ page }) => {
      await gotoTasks(page)
      const row = page.locator('.task-row').first()
      await rowAction(row, 'edit')

      const form = page.getByTestId('task-edit-form')
      await expect(form.locator('.task-image-thumb')).toHaveCount(1)

      await attachViaPicker(form)
      await expect(form.locator('.task-image-thumb')).toHaveCount(2)

      // Removing is immediate, not deferred to Save — the strip shows what the
      // task carries right now.
      await form.locator('.task-image-x').first().click()
      await expect(form.locator('.task-image-thumb')).toHaveCount(1)

      await page.fill('#task-edit-title', 'reworded after saving')
      await page.getByTestId('task-edit-save').click()

      await expect(page.locator('.task-title').first())
        .toHaveText('reworded after saving')
      await expect(row.getByTestId('task-image-count')).toHaveText('1 image')
    })

  test('the edit sheet shows the server refusal for a file that is not an image',
    async ({ page }) => {
      await gotoTasks(page)
      await rowAction(page.locator('.task-row').first(), 'edit')

      const form = page.getByTestId('task-edit-form')
      await form.getByTestId('task-image-file').setInputFiles({
        name: 'notes.png', mimeType: 'image/png',
        buffer: Buffer.from('<svg>not really</svg>'),
      })

      await expect(page.getByTestId('task-image-error'))
        .toContainText('not a supported image')
      await expect(form.locator('.task-image-thumb')).toHaveCount(1)
    })

  test('a task with no images shows no count badge', async ({ page }) => {
    await gotoTasks(page)
    await capture(page, 'plain idea, no pictures')

    const row = page.locator('.task-row').first()
    await expect(row.getByTestId('task-image-count')).toHaveCount(0)
  })
})
