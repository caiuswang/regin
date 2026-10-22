/**
 * Feature #167 S5 — the arm sheets: auto close on by default (Rules B) and a
 * workspace root as a working directory (Rules C). Every server answer is
 * stubbed with the shapes `/api/agent-runs/launch-options` (workspaces) and the
 * slice routes serve.
 */
import { expect, test } from './auth-fixture.js'

const WS = { id: 9, name: 'f167ws', root_path: '/tmp/f167-ws', repos: 2, is_git: false }
const REPO = '/tmp/f167-ws/a'

const launchOptions = (overrides = {}) => ({
  enabled: true, cwds: [REPO], workspaces: [WS], models: [], permission_modes: ['default'],
  efforts: [], default_permission_mode: 'default', detached_detail: '', providers: [],
  ...overrides,
})

const TASK = {
  id: 65, title: 'arm ws', status: 'idea', priority: 'none', body: '', kind: 'task',
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', tags: [],
  project_id: null, feature_id: null, run_count: 0, images: [], comments: [], deps: [],
  action: 'arm', waits_on: null, menu: ['arm', 'edit'],
}

async function openTaskArm(page) {
  await page.route('**/api/tasks/65/runs', route => route.fulfill({ json: { runs: [] } }))
  await page.route('**/api/tasks/65/arm-proposal',
    route => route.fulfill({ status: 404, json: { error: 'not a slice' } }))
  await page.route(/\/api\/tasks\/65(\?|$)/,
    route => route.fulfill({ json: { task: TASK, feature: null, armed: null } }))
  await page.route('**/api/agent-runs/skill-options*', route => route.fulfill({ json: { skills: [] } }))
  await page.route('**/api/agent-runs/launch-options', route => route.fulfill({ json: launchOptions() }))
  await page.route('**/api/agent-runs/worktree-options*', route => route.fulfill({ json: {
    branches: ['main'], default_base: 'main', worktrees_root: '/tmp/wt',
  } }))
  await page.goto('/tasks/65')
  await page.getByTestId('task-detail-actions').getByTestId('task-menu').click()
  await page.getByTestId('task-verb-arm').click()
  await expect(page.getByTestId('task-arm-form')).toBeVisible()
}

const FID = 343
const SLICES = [
  { n: 'S1', title: 'one', depends_on: [], base: 'f343/integration', skill: null, task_id: 81, task_status: 'idea' },
  { n: 'S2', title: 'two', depends_on: [], base: 'f343/integration', skill: null, task_id: 82, task_status: 'idea' },
]

async function openSliceArm(page) {
  const bodies = []
  await page.route(new RegExp(`/api/features/${FID}$`), route => route.fulfill({ json: {
    id: FID, title: 'Sliced', origin: 'native', project_id: null,
    external_ref: null, external_url: null, sync_error: null, body: '## Work slices',
    stage: { stage: 'spec', blurb: 'in spec', rounds: 0, claimed_at: null, open_defects: 0, escapes: 0 },
    synced_at: null, stage_live: true,
    cost: { usd: null, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} }, tokens: { input: 0, output: 0 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
    verbs: ['ship', 'abandon'],
    ship_check: { allowed: true, open_tasks: [], open_defects: [], warnings: [] },
    next_step: { kind: 'slice', label: 'Arm 2 slices', target: `/features/${FID}#slices` },
    slice_tasks: 2,
  } }))
  await page.route(new RegExp(`/api/features/${FID}/slices$`), route => route.fulfill({ json: {
    sliceable: true, refusal: null, tail_missing: false, rows: SLICES,
  } }))
  await page.route(/\/api\/tasks\/\d+\/arm-proposal$/, route => route.fulfill({ json: {
    branch: '', base: '', skill: '',
  } }))
  await page.route('**/api/agent-runs/launch-options', route => route.fulfill({ json: launchOptions() }))
  await page.route(new RegExp(`/api/features/${FID}/arm-slices$`), (route) => {
    const body = route.request().postDataJSON()
    bodies.push(body)
    return route.fulfill({ json: { armed: body.items.map(i => i.task_id), refused: [] } })
  })
  await page.goto(`/features/${FID}#slices`)
  await page.getByTestId('feature-slices').getByTestId('slices-arm-all').click()
  await expect(page.getByTestId('slice-arm-form')).toBeVisible()
  return bodies
}

test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'ignoreErrors' }) })

test('test_task_arm_sheet_auto_close_is_checked_by_default', async ({ page }) => {
  await openTaskArm(page)
  const box = page.getByTestId('task-arm-oneshot')
  await expect(page.getByText('Auto close — end the session once the agent reports done')).toBeVisible()
  await expect(box).toBeChecked()
})

test('test_slice_arm_sheet_exposes_the_auto_close_checkbox', async ({ page }) => {
  const bodies = await openSliceArm(page)
  const box = page.getByTestId('slice-arm-oneshot')
  await expect(page.getByText('Auto close — end the session once the agent reports done')).toBeVisible()
  await expect(box).toBeChecked()

  await box.click()
  await expect(box).not.toBeChecked()
  await page.getByTestId('slice-arm-submit').click()

  await expect.poll(() => bodies.length).toBe(1)
  expect(bodies[0].items.map(i => i.one_shot)).toEqual([false, false])
})

test('test_arm_offers_the_workspace_root_as_working_directory', async ({ page }) => {
  await openTaskArm(page)
  await page.getByTestId('task-arm-cwd').click()

  const options = page.getByRole('option')
  const labels = await options.allTextContents()
  const ws = labels.findIndex(l => l.includes('workspace: f167ws (2 repos)'))
  const repo = labels.findIndex(l => l.includes(REPO))
  expect(ws).toBeGreaterThan(-1)
  expect(ws).toBeLessThan(repo)

  await page.getByRole('option', { name: 'workspace: f167ws (2 repos)' }).click()
  await expect(page.getByTestId('task-arm-cwd')).toHaveText('workspace: f167ws (2 repos)')
})

test('test_worktree_is_disabled_for_a_non_git_workspace_root', async ({ page }) => {
  await openTaskArm(page)
  const worktree = page.getByTestId('live-launch-worktree')
  await expect(worktree).toBeEnabled()

  await page.getByTestId('task-arm-cwd').click()
  await page.getByRole('option', { name: 'workspace: f167ws (2 repos)' }).click()

  await expect(worktree).toBeDisabled()
  await expect(page.getByTestId('live-launch-worktree-blocked'))
    .toHaveText('the workspace root is not a git repo, so it cannot use a worktree')
})
