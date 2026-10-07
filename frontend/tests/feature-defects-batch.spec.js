/**
 * Feature #143 S5 — the FeatureDefects card files one fix task for several
 * defects, links an existing task from a dropdown, and shows fix drift. The
 * API halves live in tests/defects/test_multi_defect_fix_task.py and
 * tests/defects/test_done_consistency.py.
 */
import { test, expect } from './auth-fixture.js'

const FEATURE = 242

const LEDGER = {
  id: FEATURE, title: 'Feature workbench', origin: 'native',
  external_ref: null, external_url: null, sync_error: null, body: 'spec',
  stage: { stage: 'build', blurb: '', rounds: 1, claimed_at: null,
           open_defects: 3, escapes: 0 },
  synced_at: null, stage_live: true,
  cost: { usd: 0, sessions: 0, recalled: 0, shared: 0, gaps: {} },
  work_time: { active_work_ms: 0, gaps: {} },
  tokens: { input: 0, output: 0 },
  sessions: { rows: [], gaps: { traceless_runs: 0 } },
  defects: { open: 3, total: 4, escapes: 0 },
  coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
}

function defect(id, over = {}) {
  return {
    id, feature_id: FEATURE, feature_title: 'Feature workbench',
    symptom: `symptom ${id}`, status: 'open', round: 1,
    blamed_event_kind: 'started', missing_check: null, suspect_paths: [],
    fix_task_id: null, fix_state: null, fix_drift: false,
    triage_state: null, triage_reason: null, queue_position: null,
    images: [], opened_at: '2026-09-14 12:00:00',
    updated_at: '2026-09-14 12:00:00', closed_at: null, ...over,
  }
}

function task(id, status, over = {}) {
  return {
    id, title: `task ${id}`, body: '', status, kind: 'fix', source: 'web',
    tags: [], links: [], priority: 'none', priority_rank: 4, project_id: null,
    feature_id: FEATURE, feature_title: 'Feature workbench', assignee: null,
    assignee_name: null, external_id: null, created_by: 1,
    created_at: '2026-09-14 10:00:00', updated_at: '2026-09-14 10:00:00',
    images: [], run_count: 0, ...over,
  }
}

const page0 = items => ({ items, pagination: { total: items.length, page: 0, size: 200,
                                               has_next: false, has_prev: false } })

/** The feature page, its defects card reading `state.defects`, its tasks `state.tasks`. */
async function stubFeature(page, state) {
  await page.route(/\/api\/features\/\d+$/, route =>
    (route.request().method() === 'GET' ? route.fulfill({ json: LEDGER }) : route.fallback()))
  await page.route('**/api/tasks?**', route =>
    (route.request().method() === 'GET'
      ? route.fulfill({ json: { ...page0(state.tasks), next_cursor: null } })
      : route.fallback()))
  await page.route(/\/api\/defects\?/, route => route.fulfill({ json: page0(state.defects) }))
}

const card = page => page.getByTestId('feature-defects')

test('test_feature_defects_batch_create_one_fix_task', async ({ page }) => {
  const state = {
    defects: [defect(10), defect(11), defect(12, { fix_task_id: 99 }),
              defect(13, { status: 'closed', missing_check: 'test_x' })],
    tasks: [],
  }
  await stubFeature(page, state)
  const posted = []
  await page.route('**/api/defects/fix-task', async (route) => {
    posted.push(route.request().postDataJSON())
    state.defects = state.defects.map(d => ([10, 11].includes(d.id) ? { ...d, fix_task_id: 400 } : d))
    await route.fulfill({ json: { task_id: 400, defects: state.defects.slice(0, 2) } })
  })

  await page.goto(`/features/${FEATURE}`)
  await expect(card(page).getByTestId('defect-row-13')).toBeVisible()

  // Only open, unlinked rows can join a new task.
  await expect(card(page).getByTestId('defect-select-10')).toBeVisible()
  await expect(card(page).getByTestId('defect-select-11')).toBeVisible()
  await expect(card(page).getByTestId('defect-select-12')).toHaveCount(0)
  await expect(card(page).getByTestId('defect-select-13')).toHaveCount(0)
  await expect(page.getByTestId('feature-defects-batch')).toHaveCount(0)

  await card(page).getByRole('checkbox', { name: 'Select defect #11 for one fix task' }).check()
  await expect(page.getByTestId('feature-defects-batch-create')).toHaveText('Create one fix task (1)')
  await card(page).getByRole('checkbox', { name: 'Select defect #10 for one fix task' }).check()
  await page.getByTestId('feature-defects-batch-create').click()

  await expect(page.getByTestId('feature-defect-result')).toHaveText('#10, #11 will be fixed by task #400.')
  expect(posted).toEqual([{ defect_ids: [10, 11] }])
  // Now linked, so neither row offers a checkbox and the button is gone.
  await expect(card(page).getByTestId('defect-select-10')).toHaveCount(0)
  await expect(page.getByTestId('feature-defects-batch')).toHaveCount(0)
})

test('a refused batch names its blocking defects in place', async ({ page }) => {
  const state = { defects: [defect(10), defect(11)], tasks: [] }
  await stubFeature(page, state)
  await page.route('**/api/defects/fix-task', route => route.fulfill({
    status: 409,
    json: { error: '#11 already points at task #99 — nothing was created', blocking: [11] } }))

  await page.goto(`/features/${FEATURE}`)
  await card(page).getByTestId('defect-select-10').click()
  await card(page).getByTestId('defect-select-11').click()
  await page.getByTestId('feature-defects-batch-create').click()

  await expect(page.getByTestId('feature-defects-batch-error'))
    .toHaveText('#11 already points at task #99 — nothing was created')
  await expect(card(page).getByTestId('defect-blocking-11')).toBeVisible()
  await expect(card(page).getByTestId('defect-blocking-10')).toHaveCount(0)
  // Nothing was written, so the selection stands for the operator to fix.
  await expect(page.getByTestId('feature-defects-batch-create')).toHaveText('Create one fix task (2)')

  // Dropping the offender from the selection drops its mark with it.
  await card(page).getByTestId('defect-select-11').click()
  await expect(card(page).getByTestId('defect-blocking-11')).toHaveCount(0)
})

test('test_link_existing_task_offers_dropdown', async ({ page }) => {
  const state = {
    defects: [defect(10)],
    tasks: [task(301, 'idea'), task(302, 'running'), task(303, 'done'), task(304, 'archived')],
  }
  await stubFeature(page, state)
  const posted = []
  await page.route('**/api/defects/10/fix-item', async (route) => {
    posted.push(route.request().postDataJSON())
    await route.fulfill({ json: { defect: defect(10, { fix_task_id: 302 }) } })
  })

  await page.goto(`/features/${FEATURE}`)
  const row = card(page).getByTestId('defect-row-10')
  await row.getByTestId('defect-more-10').click()
  await page.getByRole('menuitem', { name: 'Link existing task' }).click()

  await row.getByTestId('defect-fix-item-picker-10').click()
  const options = page.getByRole('option')
  await expect(options).toHaveText(['#301 task 301', '#302 task 302'])
  await page.getByRole('option', { name: '#302 task 302' }).click()
  // The pick lands in the id field, so the two controls hold one answer.
  await expect(row.getByTestId('defect-fix-item-field-10')).toHaveValue('302')
  await row.getByTestId('defect-fix-item-submit-10').click()
  await expect.poll(() => posted).toEqual([{ task_id: 302 }])

  // Typing an id is still there for a task the dropdown does not list.
  await row.getByTestId('defect-more-10').click()
  await page.getByRole('menuitem', { name: /Re-point fix task|Link existing task/ }).click()
  await row.getByTestId('defect-fix-item-field-10').fill('303')
  await row.getByTestId('defect-fix-item-submit-10').click()
  await expect.poll(() => posted.at(-1)).toEqual({ task_id: 303 })
})

test('test_drift_row_shows_badge_and_reopen', async ({ page }) => {
  const drift = defect(242, { round: 2, fix_task_id: 345, fix_drift: true })
  const state = { defects: [drift, defect(243, { fix_task_id: 346 })], tasks: [] }
  await stubFeature(page, state)
  const writes = []
  await page.route(/\/api\/tasks\/345$/, async (route) => {
    writes.push([route.request().method(), route.request().postDataJSON()])
    state.defects = [{ ...drift, fix_drift: false }, state.defects[1]]
    await route.fulfill({ json: { task: task(345, 'idea') } })
  })
  await page.route('**/api/defects/242/fix-item', async (route) => {
    writes.push(['POST', route.request().postDataJSON()])
    const moved = { ...drift, fix_task_id: 500, fix_drift: false }
    state.defects = [moved, state.defects[1]]
    await route.fulfill({ json: { defect: moved } })
  })

  await page.goto(`/features/${FEATURE}`)
  const row = card(page).getByTestId('defect-row-242')
  await expect(row.getByTestId('defect-fix-drift-242')).toHaveText('fix done · still open')
  await expect(card(page).getByTestId('defect-fix-drift-243')).toHaveCount(0)
  await expect(row.getByTestId('defect-fix-replace-242')).toHaveText('Create fix task')

  await row.getByTestId('defect-fix-reopen-242').click()
  await expect(page.getByTestId('feature-defect-result')).toHaveText('Task #345 reopened for #242.')
  expect(writes).toEqual([['PATCH', { status: 'idea' }]])
  await expect(row.getByTestId('defect-fix-drift-242')).toHaveCount(0)
  await expect(row.getByTestId('defect-fix-reopen-242')).toHaveCount(0)
})

test('a drift row files a replacement fix task', async ({ page }) => {
  const drift = defect(242, { round: 2, fix_task_id: 345, fix_drift: true })
  const state = { defects: [drift], tasks: [] }
  await stubFeature(page, state)
  const posted = []
  await page.route('**/api/defects/242/fix-item', async (route) => {
    posted.push(route.request().postDataJSON())
    const moved = { ...drift, fix_task_id: 500, fix_drift: false }
    state.defects = [moved]
    await route.fulfill({ json: { defect: moved } })
  })

  await page.goto(`/features/${FEATURE}`)
  const row = card(page).getByTestId('defect-row-242')
  await row.getByTestId('defect-fix-replace-242').click()

  expect(posted).toEqual([{ create: true }])
  await expect(row.getByTestId('defect-fix-created-242')).toContainText('task #500')
  await expect(row.getByTestId('defect-fix-drift-242')).toHaveCount(0)
})

test('test_global_defects_list_has_no_checkboxes', async ({ page }) => {
  await page.route(/\/api\/defects\?/, route => route.fulfill({ json: {
    ...page0([defect(10), defect(11), defect(12, { feature_id: null, feature_title: null })]),
    population: 3 } }))

  await page.goto('/defects')
  await expect(page.getByTestId('defects-list').locator('> li')).toHaveCount(3)
  await expect(page.locator('[data-testid^="defect-select-"]')).toHaveCount(0)
  await expect(page.getByRole('checkbox')).toHaveCount(0)
  await expect(page.getByTestId('feature-defects-batch')).toHaveCount(0)
})
