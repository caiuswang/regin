/**
 * Feature #143 S6 — the ledger's Branch card (C-R6, C-R7) and the arm sheet's
 * base prefill from the feature's integration branch (C-R5). Every stub
 * answers as `GET /api/features/:id/branches` (`lib/features/branches.py`) does.
 */
import { test, expect } from './auth-fixture.js'

const FID = 243

function ledger(overrides = {}) {
  return {
    id: FID, title: 'Branch feature', origin: 'native', project_id: null,
    external_ref: null, external_url: null, sync_error: null, body: '',
    stage: { stage: 'spec', blurb: 'in spec', rounds: 0, claimed_at: null,
             open_defects: 0, escapes: 0 },
    synced_at: null, stage_live: true, branch: null,
    cost: { usd: null, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} },
    tokens: { input: 0, output: 0 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
    verbs: ['ship', 'abandon'],
    ship_check: { allowed: true, open_tasks: [], open_defects: [], warnings: [] },
    next_step: null, slice_tasks: 0,
    ...overrides,
  }
}

const RUNS = [
  { branch: 'f243/s2', base: 'f243/integration', task_id: 81, last_run_id: 902,
    worktree_path: '/wt/f243-s2', exists: true, merged_into_base: false },
  { branch: 'f243/s1', base: 'f243/integration', task_id: 80, last_run_id: 901,
    worktree_path: null, exists: false, merged_into_base: null },
]

async function stubLedger(page, branches) {
  const state = { row: ledger(), patches: [], reads: 0 }
  await page.route(new RegExp(`/api/features/${FID}$`), (route) => {
    if (route.request().method() !== 'PATCH') return route.fulfill({ json: state.row })
    const body = route.request().postDataJSON()
    state.patches.push(body)
    if (body.branch === 'bad..name') {
      return route.fulfill({ status: 400, json: { error: 'not a valid branch name', field: 'branch' } })
    }
    state.row = ledger({ branch: body.branch })
    return route.fulfill({ json: state.row })
  })
  await page.route(new RegExp(`/api/features/${FID}/branches$`), (route) => {
    state.reads += 1
    return route.fulfill({ json: branches(state.row) })
  })
  return state
}

test('test_feature_page_shows_branch_panel', async ({ page }) => {
  const state = await stubLedger(page, row => ({
    integration: row.branch
      ? { name: row.branch, source: 'set', exists: false, merged_into_default: null }
      : { name: 'f243/integration', source: 'convention', exists: true, merged_into_default: false },
    runs: RUNS,
  }))
  await page.goto(`/features/${FID}`)

  const card = page.getByTestId('feature-branch')
  await expect(card.getByTestId('feature-branch-name')).toHaveText('f243/integration')
  await expect(card.getByTestId('feature-branch-source')).toHaveText('convention')
  await expect(card.getByTestId('feature-branch-integration')).toContainText('exists')
  await expect(card.getByTestId('feature-branch-integration')).toContainText('not merged')

  const s2 = card.getByTestId('feature-branch-run-902')
  await expect(s2).toContainText('f243/s2')
  await expect(s2).toContainText('#81 · run 902')
  await expect(s2).toContainText('/wt/f243-s2')
  await expect(s2).toContainText('exists')
  await expect(s2).toContainText('not merged')
  const s1 = card.getByTestId('feature-branch-run-901')
  await expect(s1).toContainText('no worktree')
  await expect(s1).toContainText('missing')

  // A refused name stays in the field with the server's reason beside it.
  await card.getByTestId('feature-branch-edit').click()
  await card.getByTestId('feature-branch-input').fill('bad..name')
  await card.getByTestId('feature-branch-save').click()
  await expect(card.getByTestId('feature-branch-save-error')).toContainText('not a valid branch name')
  await expect(card.getByTestId('feature-branch-input')).toHaveValue('bad..name')
  // Cancelling a refused edit takes its alert with it.
  await card.getByTestId('feature-branch-cancel').click()
  await expect(card.getByRole('alert')).toHaveCount(0)
  await card.getByTestId('feature-branch-edit').click()

  const before = state.reads
  await card.getByTestId('feature-branch-input').fill('release/x')
  await card.getByTestId('feature-branch-save').click()
  await expect(card.getByTestId('feature-branch-name')).toHaveText('release/x')
  await expect(card.getByTestId('feature-branch-source')).toHaveText('set')
  expect(state.patches.at(-1)).toEqual({ branch: 'release/x' })
  expect(state.reads).toBeGreaterThan(before)

  // An emptied field clears the set branch rather than storing ''.
  await card.getByTestId('feature-branch-edit').click()
  await card.getByTestId('feature-branch-input').fill('')
  await card.getByTestId('feature-branch-save').click()
  await expect(card.getByTestId('feature-branch-source')).toHaveText('convention')
  expect(state.patches.at(-1)).toEqual({ branch: null })
})

test('test_branch_panel_shows_git_error', async ({ page }) => {
  await stubLedger(page, () => ({
    integration: { name: '', source: null, exists: false, merged_into_default: null },
    runs: [], error: 'repo /gone does not exist',
  }))
  await page.goto(`/features/${FID}`)

  const card = page.getByTestId('feature-branch')
  await expect(card.getByTestId('feature-branch-error')).toContainText('repo /gone does not exist')
  await expect(card.getByTestId('feature-branch-name')).toHaveText('none')
  await expect(card.getByTestId('feature-branch-runs-empty')).toBeVisible()
  // The page around the card still draws.
  await expect(page.getByTestId('feature-slices')).toBeVisible()
})

const TASK = {
  id: 82, title: 'fix on a feature', status: 'idea', priority: 'none', body: '',
  kind: 'fix', created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z', tags: [], project_id: null,
  feature_id: FID, run_count: 0, images: [], comments: [], deps: [],
  action: 'arm', waits_on: null,
  menu: ['arm', 'edit', 'deps', 'mark_done', 'archive', 'delete'],
}

async function openArm(page, { integration, proposal = null, task = TASK }) {
  const posted = []
  const branchReads = []
  await page.route(`**/api/tasks/${task.id}/runs`, route => route.fulfill({ json: { runs: [] } }))
  await page.route(`**/api/tasks/${task.id}/arm-proposal`, route => (proposal
    ? route.fulfill({ json: proposal })
    : route.fulfill({ status: 404, json: { error: 'not a slice' } })))
  await page.route(new RegExp(`/api/tasks/${task.id}(\\?|$)`),
    route => route.fulfill({ json: { task, feature: null, armed: null } }))
  await page.route(`**/api/tasks/${task.id}/arm`, (route) => {
    posted.push(route.request().postDataJSON())
    return route.fulfill({ json: { task, armed: null } })
  })
  await page.route(/\/api\/features\/\d+\/branches$/, (route) => {
    branchReads.push(route.request().url())
    return route.fulfill({ json: {
      integration: { name: integration, source: integration ? 'set' : null,
                     exists: !!integration, merged_into_default: null },
      runs: [] } })
  })
  await page.route('**/api/agent-runs/launch-options', route => route.fulfill({ json: {
    enabled: true, cwds: [], models: ['opus'], permission_modes: ['default'],
    efforts: [], default_permission_mode: 'default',
  } }))
  await page.route('**/api/agent-runs/worktree-options*', route => route.fulfill({ json: {
    branches: ['main', 'release/x', 'f243/integration'], default_base: 'main',
    worktrees_root: '/wt' } }))
  await page.goto(`/tasks/${task.id}`)
  await page.getByTestId('task-detail-actions').getByTestId('task-menu').click()
  await page.getByTestId('task-verb-arm').click()
  await expect(page.getByTestId('task-arm-form')).toBeVisible()
  return { posted, branchReads }
}

async function armOnWorktree(page, posted) {
  if (!await page.getByTestId('live-launch-worktree').isChecked()) {
    await page.getByTestId('live-launch-worktree').check()
  }
  await page.getByTestId('live-launch-worktree-branch').fill('f243/fix-1')
  await page.getByTestId('task-arm-submit').click()
  await expect.poll(() => posted.length).toBe(1)
  return posted[0].worktree
}

test('test_arm_prefill', async ({ page }) => {
  const { posted } = await openArm(page, { integration: 'release/x' })
  await page.getByTestId('live-launch-worktree').check()
  await expect(page.getByTestId('live-launch-worktree-base')).toContainText('release/x')
  await expect(page.getByTestId('task-arm-base-note')).toHaveCount(0)
  await page.getByTestId('live-launch-worktree-branch').fill('f243/fix-1')
  await page.getByTestId('task-arm-submit').click()
  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0].worktree).toEqual({ branch: 'f243/fix-1', base: 'release/x' })
})

test('test_arm_prefill_says_when_the_repo_lacks_the_integration', async ({ page }) => {
  const { posted } = await openArm(page, { integration: 'gone/x' })
  await page.getByTestId('live-launch-worktree').check()
  await expect(page.getByTestId('task-arm-base-note')).toContainText('integrates on gone/x')
  await expect(page.getByTestId('task-arm-base-note')).toContainText('forks from main')
  expect((await armOnWorktree(page, posted)).base).toBe('main')
})

test('test_arm_prefill_empty_integration_leaves_base_alone', async ({ page }) => {
  const { posted } = await openArm(page, { integration: '' })
  expect((await armOnWorktree(page, posted)).base).toBe('main')
})

test('test_arm_prefill_never_overrides_a_slice_proposal', async ({ page }) => {
  const { posted } = await openArm(page, { integration: 'release/x', proposal: {
    branch: 'f243/s3', base: 'f243/integration', skill: null, verify_cmd: null } })
  await expect(page.getByTestId('live-launch-worktree-branch')).toHaveValue('f243/s3')
  await page.getByTestId('task-arm-submit').click()
  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0].worktree).toEqual({ branch: 'f243/s3', base: 'f243/integration' })
})

test('test_arm_without_feature_reads_no_branches', async ({ page }) => {
  const { posted, branchReads } = await openArm(page, {
    integration: 'release/x', task: { ...TASK, id: 83, feature_id: null } })
  expect((await armOnWorktree(page, posted)).base).toBe('main')
  expect(branchReads).toEqual([])
})
