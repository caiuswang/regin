/**
 * Slicing from the UI (#119 S3, R3): the ledger's Slices panel transcribes the
 * spec's table and arms every idea slice in one sheet, prefilled from each
 * row's arm-proposal. The expanded Features row says how many are not armed.
 * Every fact drawn here is the server's — the stubs answer as
 * `lib/tasks/feature_slices` does.
 */
import { test, expect } from './auth-fixture.js'

const FID = 242

function ledger(overrides = {}) {
  return {
    id: FID, title: 'Sliced feature', origin: 'native', project_id: null,
    external_ref: null, external_url: null, sync_error: null,
    body: '## Work slices\n\n| # | Slice |',
    stage: { stage: 'spec', blurb: 'in spec', rounds: 0, claimed_at: null,
             open_defects: 0, escapes: 0 },
    synced_at: null, stage_live: true,
    cost: { usd: null, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} },
    tokens: { input: 0, output: 0 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
    verbs: ['ship', 'abandon'],
    ship_check: { allowed: true, open_tasks: [], open_defects: [], warnings: [] },
    next_step: { kind: 'slice', label: 'Transcribe 3 slices', target: `/features/${FID}#slices` },
    slice_tasks: 0,
    ...overrides,
  }
}

const ROWS = [
  { n: 'S1', title: 'contracts', depends_on: [], base: 'f242/integration', skill: 'goal-verified' },
  { n: 'S2', title: 'runner', depends_on: ['S1'], base: 'f242/integration', skill: 'goal-verified' },
  { n: 'S3', title: 'notice', depends_on: ['S1'], base: 'f242/integration', skill: 'goal-verified' },
]
const IDS = { S1: 71, S2: 72, S3: 73 }

function report(status) {
  return {
    sliceable: true, refusal: null, tail_missing: false,
    rows: ROWS.map(r => ({
      ...r,
      task_id: status[r.n] ? IDS[r.n] : null,
      task_status: status[r.n] || null,
    })),
  }
}

async function stubSlices(page, { refuse = {} } = {}) {
  const state = { status: {}, transcribed: 0, armBodies: [] }
  await page.route(new RegExp(`/api/features/${FID}$`), route => route.fulfill({ json: ledger() }))
  await page.route(new RegExp(`/api/features/${FID}/slices$`),
    route => route.fulfill({ json: report(state.status) }))
  await page.route(new RegExp(`/api/features/${FID}/transcribe$`), (route) => {
    state.transcribed += 1
    state.status = { S1: 'idea', S2: 'idea', S3: 'idea' }
    return route.fulfill({ json: { created: [71, 72, 73], updated: [], refused: [], slices: [] } })
  })
  await page.route(/\/api\/tasks\/(\d+)\/arm-proposal$/, (route) => {
    const id = Number(route.request().url().match(/tasks\/(\d+)\//)[1])
    const row = ROWS.find(r => IDS[r.n] === id)
    return route.fulfill({ json: {
      branch: `f242/${row.n.toLowerCase()}`, base: row.base,
      skill: row.skill,
    } })
  })
  await page.route(new RegExp(`/api/features/${FID}/arm-slices$`), (route) => {
    const body = route.request().postDataJSON()
    state.armBodies.push(body)
    const armed = []
    const refused = []
    for (const item of body.items) {
      if (refuse[item.task_id]) {
        refused.push({ task_id: item.task_id, reason: refuse[item.task_id] })
        continue
      }
      armed.push(item.task_id)
      const n = Object.keys(IDS).find(k => IDS[k] === item.task_id)
      state.status[n] = 'armed'
    }
    return route.fulfill({ json: { armed, refused } })
  })
  await page.route('**/api/agent-runs/launch-options', route => route.fulfill({ json: {
    enabled: true, models: [], permission_modes: ['default'], efforts: [],
    cwds: ['/r/regin'], default_permission_mode: 'default',
  } }))
  return state
}

test('test_feature_slice_panel_transcribe_and_arm_all', async ({ page }) => {
  const state = await stubSlices(page)
  await page.goto(`/features/${FID}#slices`)

  const panel = page.getByTestId('feature-slices')
  await expect(panel.getByTestId('slice-row-S1')).toContainText('not transcribed')
  await expect(panel.getByTestId('slices-arm-all')).toHaveCount(0)

  await panel.getByTestId('slices-transcribe').click()
  await expect.poll(() => state.transcribed).toBe(1)
  for (const n of ['S1', 'S2', 'S3']) {
    await expect(panel.getByTestId(`slice-task-${n}`)).toContainText(`#${IDS[n]} idea`)
  }

  await panel.getByTestId('slices-arm-all').click()
  await expect(panel.getByTestId('slices-arm-all')).toHaveText('Arm 3')
  // Each row opens with its own slice-table proposal.
  await expect(page.getByTestId('slice-arm-branch-S2')).toHaveValue('f242/s2')
  await expect(page.getByTestId('slice-arm-base-S2')).toHaveValue('f242/integration')
  await expect(page.getByTestId('slice-arm-skill-S2')).toHaveValue('goal-verified')

  await page.getByTestId('slice-arm-submit').click()
  await expect.poll(() => state.armBodies.length).toBe(1)
  const items = state.armBodies[0].items
  expect(items.map(i => i.task_id)).toEqual([71, 72, 73])
  expect(items[1]).toMatchObject({
    worktree: { branch: 'f242/s2', base: 'f242/integration' },
    skill: { name: 'goal-verified' },
  })

  // One request, no run: the sheet closes and the panel shows three armed.
  await expect(page.getByTestId('slice-arm-form')).toHaveCount(0)
  for (const n of ['S1', 'S2', 'S3']) {
    await expect(panel.getByTestId(`slice-task-${n}`)).toContainText('armed')
  }
  await expect(panel.getByTestId('slices-arm-all')).toHaveCount(0)
})

test('an arm-all refusal is named on its row and the sheet stays open', async ({ page }) => {
  const state = await stubSlices(page, { refuse: { 72: 'unknown skill name' } })
  state.status = { S1: 'idea', S2: 'idea', S3: 'idea' }
  await page.goto(`/features/${FID}`)

  await page.getByTestId('slices-arm-all').click()
  await expect(page.getByTestId('slice-arm-branch-S1')).toHaveValue('f242/s1')
  await page.getByTestId('slice-arm-submit').click()

  await expect(page.getByTestId('slice-arm-refusal-S2')).toHaveText('unknown skill name')
  await expect(page.getByTestId('slice-arm-row-S1')).toHaveCount(0)
  await expect(page.getByTestId('slice-arm-submit')).toHaveText('Arm 1')
})

test('a refused spec shows the refusal verbatim and offers no Transcribe', async ({ page }) => {
  await stubSlices(page)
  await page.route(new RegExp(`/api/features/${FID}/slices$`), route => route.fulfill({ json: {
    sliceable: false, tail_missing: true, rows: [],
    refusal: { message: 'this spec cannot be sliced: no Acceptance section', missing: ['no Acceptance section'] },
  } }))
  await page.goto(`/features/${FID}`)
  const panel = page.getByTestId('feature-slices')
  await expect(panel.getByTestId('slices-refusal')).toHaveText('this spec cannot be sliced: no Acceptance section')
  await expect(panel.getByTestId('slices-transcribe')).toHaveCount(0)
})

test('the expanded features row counts idea slices and links to the panel', async ({ page }) => {
  const row = { id: FID, title: 'Sliced feature', origin: 'native', external_ref: null, stage: 'spec',
    blurb: '', rounds: 0, sessions: 0, tasks: 3, tasks_done: 0, cost_usd: null, gaps: {},
    defects: { total: 0, open: 0, escapes: 0 }, last_activity: '2026-09-27T06:00:00',
    next_step: { kind: 'arm_slices', label: 'Arm 2 slices', target: `/features/${FID}#slices` } }
  await page.route('**/api/features?*', route => route.fulfill({ json: {
    items: [row], pagination: { total: 1, page: 0, size: 50, has_next: false, has_prev: false },
    population: 1, applied: { sort: 'activity:desc' },
    extras: { stage_counts: { all: 1, spec: 1, building: 0, shipped: 0, escaped: 0 },
      unattributed_defects: 0,
      attribution: { sessions_pct: 0, declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 } },
  } }))
  await stubSlices(page)
  await page.route(new RegExp(`/api/features/${FID}/slices$`), route => route.fulfill({
    json: report({ S1: 'armed', S2: 'idea', S3: 'idea' }) }))

  await page.goto('/features')
  await page.getByTestId(`feature-toggle-${FID}`).click()
  const ideas = page.getByTestId('feature-active-ideas')
  await expect(ideas).toContainText('2 not armed')
  await ideas.getByTestId('feature-active-arm').click()
  await expect(page).toHaveURL(new RegExp(`/features/${FID}#slices$`))
  await expect(page.getByTestId('feature-slices')).toBeVisible()
})

test('the arm sheet prefills a slice from its proposal before the remembered prefs (R4b)', async ({ page }) => {
  // The last arm in this browser carried another task's cwd; once
  // the slice row has spoken, only its skill and model may carry over.
  await page.addInitScript(() => localStorage.setItem(
    'regin.tasks.arm.last',
    JSON.stringify({ cwd: '/r/regin', model: 'opus' })))
  const task = {
    id: 72, title: 'S2 — runner', status: 'idea', priority: 'none', body: '',
    kind: 'build', created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z', tags: [], project_id: null,
    feature_id: FID, run_count: 0, images: [], comments: [], deps: [],
    action: 'arm', waits_on: null, menu: ['edit', 'deps', 'mark_done', 'archive', 'delete'],
  }
  await page.route(/\/api\/tasks\/72(\?|$)/, route =>
    route.fulfill({ json: { task, feature: null, armed: null } }))
  await page.route(/\/api\/tasks\/72\/runs$/, route => route.fulfill({ json: { runs: [] } }))
  await page.route(/\/api\/tasks\/72\/arm-proposal$/, route => route.fulfill({ json: {
    branch: 'f242/s2', base: 'f242/integration', skill: 'goal-verified',
  } }))
  await page.route('**/api/agent-runs/launch-options', route => route.fulfill({ json: {
    enabled: true, cwds: ['/r/regin'], models: ['opus'],
    permission_modes: ['default'], efforts: [], default_permission_mode: 'default',
  } }))
  await page.route('**/api/agent-runs/worktree-options*', route =>
    route.fulfill({ json: { branches: ['f242/integration'], default_base: 'main' } }))

  await page.goto('/tasks/72')
  await page.getByTestId('task-detail-actions').getByTestId('task-action-arm').click()

  await expect(page.getByTestId('task-arm-form')).toBeVisible()
  await expect(page.getByTestId('live-launch-worktree-branch')).toHaveValue('f242/s2')
  await expect(page.getByTestId('task-arm-cwd')).not.toContainText('/r/regin')
  await expect(page.getByTestId('task-arm-model')).toContainText('opus')
  await expect(page.getByTestId('task-arm-recalled')).toBeVisible()
})
