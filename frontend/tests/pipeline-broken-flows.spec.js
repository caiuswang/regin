/**
 * Feature #119, R1 — the Pipeline flows that were broken outright (B1, B3–B6).
 * B2 is an API rule and lives in tests/pipeline/test_overview.py.
 */
import { test, expect } from './auth-fixture.js'
import { BASELINE_TRACE } from './helpers/fixtures.js'

function ledger(overrides = {}) {
  return {
    id: 242, title: 'Feature ledger', origin: 'native', body: 'the spec',
    external_ref: null, external_url: null, sync_error: null,
    stage: {
      stage: 'building', blurb: 'being built', rounds: 1, claimed_at: null,
      open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true,
    cost: { usd: 1, sessions: 1, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: null, gaps: {} },
    tokens: { input: 0, output: 0 },
    sessions: {
      rows: [{
        trace_id: 'abcdef0123456789', short_trace: 'abcdef01',
        date: '2026-09-01', title: 'built the ledger', cost_usd: 1,
        provenance: 'declared', round: 1, is_primary: true,
      }],
      gaps: { traceless_runs: 0 },
    },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 1, all_sessions: 2, declared_usd: 1, all_usd: 2 },
    images: [],
    ...overrides,
  }
}

test('test_declare_feature_dialog_attributes_chosen_row', async ({ page }) => {
  let declared = []
  const posted = []
  await page.route(/\/api\/sessions\/[^/]+\/features$/, (route) =>
    route.fulfill({ json: { trace_id: BASELINE_TRACE, features: declared } }))
  await page.route(/\/api\/features(\?.*)?$/, (route) => route.fulfill({ json: {
    items: [{ id: 12, title: 'Feature ledger', stage: 'building' },
            { id: 13, title: 'Other feature', stage: 'spec' }],
    total: 2, unattributed_defects: 0,
    coverage: { declared_sessions: 0, all_sessions: 1, declared_usd: 0, all_usd: 1 },
  } }))
  await page.route(/\/api\/features\/\d+\/attribute$/, (route) => {
    posted.push({ path: new URL(route.request().url()).pathname,
                  body: route.request().postDataJSON() })
    declared = [{ feature_id: 12, title: 'Feature ledger', provenance: 'declared',
                  is_primary: false, round: 1, task_id: null }]
    return route.fulfill({ json: { ok: true } })
  })

  await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
  await page.getByTestId('session-attribution')
    .getByRole('button', { name: 'Declare' }).click()

  // Search text is never an id: until a row is chosen there is nothing to post.
  await page.getByPlaceholder(/search feature title/).fill('ledger')
  const declare = page.getByRole('button', { name: 'Declare', exact: true }).last()
  await expect(declare).toBeDisabled()

  await page.getByTestId('declare-feature-row-12').click()
  await declare.click()

  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0].path).toBe('/api/features/12/attribute')
  expect(posted[0].body.trace_id).toBe(BASELINE_TRACE)
  await expect(page.getByTestId('session-attribution').locator('.attr-key'))
    .toHaveAttribute('href', '/features/12')
})

test('test_defect_merge_lands_on_keeper', async ({ page }) => {
  const row = (id, symptom) => ({
    id, feature_id: 81, feature_title: 'The ledger', symptom, status: 'open',
    round: 0, blamed_event_kind: 'started', missing_check: null,
    suspect_paths: [], fix_task_id: null, images: [],
    opened_at: '2026-09-14 12:00:00', closed_at: null,
  })
  const rows = { 7: row(7, 'the duplicate'), 9: row(9, 'the keeper') }
  await page.route(/\/api\/defects\/\d+$/, (route) => {
    const id = Number(route.request().url().match(/\/defects\/(\d+)$/)[1])
    return rows[id]
      ? route.fulfill({ json: { defect: rows[id] } })
      : route.fulfill({ status: 404, json: { error: `no defect ${id}` } })
  })
  await page.route('**/api/defects/7/merge', (route) => {
    delete rows[7]
    return route.fulfill({ json: { defect: rows[9] } })
  })

  await page.goto('/defects/7')
  await page.getByTestId('defect-more-7').click()
  await page.getByRole('menuitem', { name: 'Merge into…' }).click()
  await page.getByPlaceholder('the defect to KEEP, e.g. 17').fill('9')
  await page.getByTestId('defect-merge-submit-7').click()

  await expect(page).toHaveURL(/\/defects\/9$/)
  await expect(page.getByTestId('defect-row-9')).toBeVisible()
  await expect(page.getByTestId('defect-detail-error')).toHaveCount(0)
  await expect(page.getByRole('status')).toContainText('#7 folded into #9')
})

test('test_task_detail_rearm_prefills_pending_params', async ({ page }) => {
  // The last arm in this browser was a different task's — the sheet must not
  // prefer it over the attempt this task already has pending.
  await page.addInitScript(() => localStorage.setItem(
    'regin.tasks.arm.last',
    JSON.stringify({ cwd: '/r/regin', verify_cmd: 'from another task' })))
  const task = {
    id: 51, title: 'slice one', status: 'armed', priority: 'none', body: '',
    kind: 'task', created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z', tags: [], project_id: null,
    feature_id: null, run_count: 0, images: [], comments: [], deps: [],
    action: 'run', waits_on: null,
    menu: ['disarm', 'arm', 'edit', 'deps', 'mark_done', 'archive', 'delete'],
  }
  const armed = {
    status: 'armed', attempt: 1, verify_cmd: 'pytest tests/pipeline', autorun: false,
    params: { cwd: '/r/regin', worktree: { branch: 'x', base: 'main' } },
  }
  await page.route(/\/api\/tasks\/51(\?|$)/, (route) =>
    route.fulfill({ json: { task, feature: null, armed } }))
  await page.route('**/api/agent-runs/launch-options', (route) =>
    route.fulfill({ json: {
      enabled: true, cwds: ['/r/regin'], models: ['opus'],
      permission_modes: ['default'], efforts: [], default_permission_mode: 'default',
    } }))
  await page.route('**/api/agent-runs/worktree-options*', (route) =>
    route.fulfill({ json: { branches: ['main'], default_base: 'main' } }))

  await page.goto('/tasks/51')
  await page.getByTestId('task-detail-actions').getByTestId('task-menu').click()
  await page.getByTestId('task-verb-arm').click()

  await expect(page.getByTestId('task-arm-form')).toBeVisible()
  await expect(page.getByTestId('live-launch-worktree-branch')).toHaveValue('x')
  await expect(page.getByTestId('task-arm-verify')).toHaveValue('pytest tests/pipeline')
  await expect(page.getByTestId('task-arm-recalled')).toHaveCount(0)
})

test('test_unattribute_refusal_stays_inline', async ({ page }) => {
  await page.route(/\/api\/features\/242$/, (route) =>
    route.fulfill({ json: ledger() }))
  await page.route(/\/api\/features\/242\/attribute\/.+$/, (route) =>
    route.fulfill({ status: 409, json: {
      error: 'abcdef01 is attributed through a run, which is withdrawn by moving the task' } }))

  await page.goto('/features/242')
  await page.getByRole('button', { name: /Un-declare abcdef01/ }).click()
  await page.getByRole('button', { name: 'Unattribute', exact: true }).click()

  const refusal = page.getByTestId('undeclare-refusal')
  await expect(refusal).toContainText('attributed through a run')
  // The page it was fired from is still there.
  await expect(page.getByTestId('feature-spec')).toBeVisible()
  await expect(page.getByRole('button', { name: /Un-declare abcdef01/ })).toBeVisible()
})

test('test_tracked_feature_spec_is_read_only', async ({ page }) => {
  await page.route(/\/api\/features\/242$/, (route) => route.fulfill({ json: ledger({
    origin: 'linear', external_ref: 'CAI-274',
    external_url: 'https://linear.app/x/issue/CAI-274',
    synced_at: '2026-09-13 05:40:00', stage_live: false,
    body: 'the issue text',
  }) }))

  await page.goto('/features/242')
  const spec = page.getByTestId('feature-spec')
  await expect(spec).toContainText('the issue text')
  await expect(page.getByTestId('feature-spec-edit')).toHaveCount(0)
  await expect(page.getByTestId('feature-spec-tracker-link'))
    .toHaveAttribute('href', 'https://linear.app/x/issue/CAI-274')
  await expect(page.getByTestId('feature-spec-tracker-link')).toHaveText('Edit in Linear →')
  // Refresh stays reachable on the stage line.
  await expect(page.getByRole('button', { name: /Refresh/ })).toBeVisible()
})

test('a native feature spec stays editable', async ({ page }) => {
  await page.route(/\/api\/features\/242$/, (route) =>
    route.fulfill({ json: ledger() }))
  await page.goto('/features/242')
  await expect(page.getByTestId('feature-spec-edit')).toBeVisible()
  await expect(page.getByTestId('feature-spec-tracked')).toHaveCount(0)
})
