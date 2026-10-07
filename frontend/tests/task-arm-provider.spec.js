/**
 * Feature #140 S4 — the arm sheets pick a provider from launch-options'
 * `providers`, and show and send only what that provider declares
 * (Invariant 4). The stubs answer as `registry.launch_provider_rows` does.
 */
import { test, expect } from './auth-fixture.js'
import { pickChoice } from './helpers/choice.js'

const CLAUDE = {
  id: 'claude', display_name: 'Claude Code', binary: 'claude', available: true,
  launch_modes: ['sdk', 'tmux'], fields: ['model', 'permission_mode', 'effort'],
  labels: { sdk: 'Agent SDK', tmux: 'tmux' }, models: ['opus', 'sonnet'], default_model: '',
}
const KIMI = {
  id: 'kimi', display_name: 'Kimi Code', binary: 'kimi', available: true,
  launch_modes: ['sdk'], fields: ['model'], labels: { sdk: 'ACP' },
  models: [], default_model: '',
}
const CODEX = {
  id: 'codex2', display_name: 'Other CLI', binary: 'othercli', available: false,
  launch_modes: ['sdk'], fields: [], labels: { sdk: 'Channel' },
  models: [], default_model: '',
}

const TASK = {
  id: 64, title: 'provider slice', status: 'idea', priority: 'none', body: '',
  kind: 'task', created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z', tags: [], project_id: null,
  feature_id: null, run_count: 0, images: [], comments: [], deps: [],
  action: 'arm', waits_on: null,
  menu: ['arm', 'edit', 'deps', 'mark_done', 'archive', 'delete'],
}

async function pick(page, testid, label) {
  await page.getByTestId(testid).click()
  await page.getByRole('option', { name: label, exact: true }).click()
}

async function openArmSheet(page, { providers = [CLAUDE, KIMI], runs = [] } = {}) {
  const posted = []
  const skillQueries = []
  await page.route('**/api/tasks/64/runs', route => route.fulfill({ json: { runs } }))
  await page.route('**/api/tasks/64/arm-proposal', route =>
    route.fulfill({ status: 404, json: { error: 'not a slice' } }))
  await page.route(/\/api\/tasks\/64(\?|$)/, route =>
    route.fulfill({ json: { task: TASK, feature: null, armed: null } }))
  await page.route('**/api/tasks/64/arm', (route) => {
    posted.push(route.request().postDataJSON())
    return route.fulfill({ json: { task: TASK, armed: null } })
  })
  await page.route('**/api/agent-runs/skill-options*', (route) => {
    const url = new URL(route.request().url())
    skillQueries.push(url.searchParams.get('provider'))
    return route.fulfill({ json: { skills: [] } })
  })
  await page.route('**/api/agent-runs/launch-options', route => route.fulfill({ json: {
    enabled: true, cwds: [], models: ['opus', 'sonnet'],
    permission_modes: ['default', 'acceptEdits'], efforts: ['high'],
    default_permission_mode: 'default', detached_detail: '', providers,
  } }))
  await page.goto('/tasks/64')
  await page.getByTestId('task-detail-actions').getByTestId('task-menu').click()
  await page.getByTestId('task-verb-arm').click()
  await expect(page.getByTestId('task-arm-form')).toBeVisible()
  return { posted, skillQueries }
}

test('test_arm_sheet_provider_picker_lists_available_providers', async ({ page }) => {
  await openArmSheet(page, { providers: [CLAUDE, KIMI, CODEX] })

  const picker = page.getByTestId('task-arm-provider')
  await expect(picker).toContainText('Claude Code')
  await picker.click()
  await expect(page.getByRole('option', { name: 'Kimi Code', exact: true })).toBeEnabled()
  const missing = page.getByRole('option', { name: 'Other CLI — othercli not on PATH' })
  await expect(missing).toHaveAttribute('data-disabled', /.*/)
})

test('test_kimi_arm_hides_claude_only_fields', async ({ page }) => {
  const { skillQueries } = await openArmSheet(page)
  await expect(page.getByTestId('task-arm-mode')).toBeVisible()
  await expect(page.getByTestId('task-arm-effort')).toBeVisible()

  await pick(page, 'task-arm-provider', 'Kimi Code')
  await expect(page.getByTestId('task-arm-mode')).toHaveCount(0)
  await expect(page.getByTestId('task-arm-effort')).toHaveCount(0)
  await expect(page.getByTestId('task-arm-model')).toBeVisible()
  // Kimi has no Claude aliases to offer.
  await page.getByTestId('task-arm-model').click()
  await expect(page.getByRole('option', { name: 'opus', exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')
  // Launch offers only the declared channel, under the provider's own label.
  await expect(page.getByTestId('task-arm-launch')).toContainText('ACP')
  await expect(page.getByTestId('task-arm-launch')).not.toContainText('tmux')
  // The skill menu is the picked provider's.
  await expect.poll(() => skillQueries.at(-1)).toBe('kimi')
})

test('test_kimi_arm_body_carries_provider_and_no_claude_fields', async ({ page }) => {
  const { posted } = await openArmSheet(page)
  await pick(page, 'task-arm-mode', 'acceptEdits')
  await pick(page, 'task-arm-provider', 'Kimi Code')
  await page.getByTestId('task-arm-submit').click()

  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0].provider).toBe('kimi')
  expect(posted[0]).not.toHaveProperty('permission_mode')
  expect(posted[0]).not.toHaveProperty('effort')
  expect(posted[0].launch).toBe('sdk')
  expect(posted[0].model).toBe('')
})

test('test_arm_prefs_drop_model_on_provider_switch', async ({ page }) => {
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('seeded')) {
      sessionStorage.setItem('seeded', '1')
      localStorage.setItem('regin.tasks.arm.last', JSON.stringify({ model: 'opus' }))
    }
  })
  const { posted } = await openArmSheet(page)
  await expect(page.getByTestId('task-arm-model')).toContainText('opus')

  await pick(page, 'task-arm-provider', 'Kimi Code')
  await expect(page.getByTestId('task-arm-model')).not.toContainText('opus')
  await page.getByTestId('task-arm-submit').click()
  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0].model).toBe('')

  // The switch is what is remembered, so the next sheet opens on kimi.
  const stored = await page.evaluate(
    () => JSON.parse(localStorage.getItem('regin.tasks.arm.last')))
  expect(stored.provider).toBe('kimi')
})

test('test_arm_prefs_keep_provider_of_a_run_that_named_none', async ({ page }) => {
  // A run armed before providers existed ran on claude with a claude model;
  // a later kimi arm must not re-aim it at kimi with that model.
  await page.addInitScript(() => localStorage.setItem(
    'regin.tasks.arm.last', JSON.stringify({ provider: 'kimi' })))
  const runs = [{ id: 9, attempt: 1, status: 'failed', trace_id: 'x', live: false,
                  params: { model: 'opus' }, verify_cmd: null }]
  const { posted } = await openArmSheet(page, { runs })
  await expect(page.getByTestId('task-arm-provider')).toContainText('Claude Code')
  await expect(page.getByTestId('task-arm-model')).toContainText('opus')
  await page.getByTestId('task-arm-submit').click()
  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0]).toMatchObject({ provider: 'claude', model: 'opus' })
})

test('test_claude_arm_sheet_unchanged', async ({ page }) => {
  const { posted, skillQueries } = await openArmSheet(page)
  for (const id of ['task-arm-mode', 'task-arm-model', 'task-arm-effort']) {
    await expect(page.getByTestId(id)).toBeVisible()
  }
  await expect(page.getByTestId('task-arm-launch')).toContainText('SDK')
  await pickChoice(page, 'task-arm-launch', 'tmux')
  await pick(page, 'task-arm-model', 'sonnet')
  await page.getByTestId('task-arm-submit').click()

  await expect.poll(() => posted.length).toBe(1)
  expect(Object.keys(posted[0]).sort()).toEqual([
    'autorun', 'cwd', 'effort', 'launch', 'model', 'one_shot',
    'permission_mode', 'provider', 'verify_cmd',
  ])
  expect(posted[0]).toMatchObject({ provider: 'claude', model: 'sonnet', launch: 'tmux' })
  expect(skillQueries[0]).toBe('claude')
})

test('test_slice_arm_sheet_offers_provider', async ({ page }) => {
  const FID = 243
  const bodies = []
  await page.route(new RegExp(`/api/features/${FID}$`), route => route.fulfill({ json: {
    id: FID, title: 'Kimi slices', origin: 'native', project_id: null,
    external_ref: null, external_url: null, sync_error: null,
    body: '## Work slices', synced_at: null, stage_live: true,
    stage: { stage: 'spec', blurb: 'in spec', rounds: 0, claimed_at: null,
             open_defects: 0, escapes: 0 },
    cost: { usd: null, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} }, tokens: { input: 0, output: 0 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
    verbs: [], ship_check: { allowed: true, open_tasks: [], open_defects: [], warnings: [] },
    next_step: null, slice_tasks: 1,
  } }))
  await page.route(new RegExp(`/api/features/${FID}/slices$`), route => route.fulfill({ json: {
    sliceable: true, refusal: null, tail_missing: false,
    rows: [{ n: 'S1', title: 'only', depends_on: [], base: 'main', skill: null,
             verify: null, task_id: 81, task_status: 'idea' }],
  } }))
  await page.route(/\/api\/tasks\/81\/arm-proposal$/, route => route.fulfill({ json: {
    branch: 'f243/s1', base: 'main', skill: '', verify_cmd: '' } }))
  await page.route(new RegExp(`/api/features/${FID}/arm-slices$`), (route) => {
    bodies.push(route.request().postDataJSON())
    return route.fulfill({ json: { armed: [81], refused: [] } })
  })
  await page.route('**/api/agent-runs/launch-options', route => route.fulfill({ json: {
    enabled: true, models: [], permission_modes: [], efforts: [], cwds: [],
    default_permission_mode: 'default', providers: [CLAUDE, KIMI],
  } }))
  await page.goto(`/features/${FID}#slices`)
  await page.getByTestId('feature-slices').getByTestId('slices-arm-all').click()
  await expect(page.getByTestId('slice-arm-form')).toBeVisible()

  await expect(page.getByTestId('slice-arm-provider')).toContainText('Claude Code')
  await pick(page, 'slice-arm-provider', 'Kimi Code')
  await page.getByTestId('slice-arm-submit').click()
  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0].items[0]).toMatchObject({ task_id: 81, provider: 'kimi' })
})
