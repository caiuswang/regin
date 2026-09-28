/**
 * Feature lifecycle verbs on the ledger (#119 S2): ship asks first and names
 * the open work it would leave, reopen states its round cost, the title
 * renames in place, unattribute confirms, and Build steps aside for slices.
 * Every fact drawn here is the server's — the stubs carry `verbs`,
 * `ship_check` and `next_step` exactly as `_ledger_body` serves them.
 */
import { test, expect } from './auth-fixture.js'

function ledger(overrides = {}) {
  return {
    id: 242, title: 'Feature ledger', origin: 'native',
    external_ref: null, external_url: null, sync_error: null,
    stage: {
      stage: 'building', blurb: 'being built',
      rounds: 1, claimed_at: null, open_defects: 1, escapes: 0,
    },
    synced_at: null, stage_live: true,
    cost: { usd: null, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} },
    tokens: { input: 0, output: 0 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 1, total: 1, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0,
                declared_usd: 0, all_usd: 0 },
    verbs: ['ship', 'abandon'],
    ship_check: {
      allowed: true,
      open_tasks: [{ id: 71, title: 'S1 — build it', status: 'armed' }],
      open_defects: [{ id: 88, symptom: 'the ship button double-fires' }],
      warnings: [],
    },
    next_step: { kind: 'run', label: 'Run the next task', target: '/tasks/71' },
    slice_tasks: 0,
    ...overrides,
  }
}

async function stubLedger(page, body) {
  await page.route(/\/api\/features\/242$/, (route) =>
    route.fulfill({ json: body }))
}

test('test_feature_ship_confirm_lists_open_work', async ({ page }) => {
  await stubLedger(page, ledger())
  let posted = 0
  await page.route(/\/api\/features\/242\/ship$/, (route) => {
    posted += 1
    return route.fulfill({ json: ledger({ verbs: ['reopen', 'abandon'] }) })
  })

  await page.goto('/features/242')
  await page.getByTestId('ship-feature').click()

  const dialog = page.getByText('Claim "Feature ledger" shipped?')
  await expect(dialog).toBeVisible()
  await expect(page.getByText(/Open tasks: #71 \(armed\)/)).toBeVisible()
  await expect(page.getByText(/Open defects: #88/)).toBeVisible()
  expect(posted).toBe(0)

  await page.getByRole('button', { name: 'Ship', exact: true }).last().click()
  await expect.poll(() => posted).toBe(1)
})

test('a tracked feature ship warns about the tracker', async ({ page }) => {
  await stubLedger(page, ledger({
    origin: 'linear', external_ref: 'LIN-9', stage_live: false,
    ship_check: {
      allowed: true, open_tasks: [], open_defects: [],
      warnings: ['Linear still says in progress; the next Refresh will count a walk-back unless you move it there.'],
    },
  }))
  await page.goto('/features/242')
  await page.getByTestId('ship-feature').click()
  await expect(page.getByText(/Linear still says in progress/)).toBeVisible()
  // A tracked title is the issue's: Refresh rewrites it.
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.locator('button[data-testid="feature-title"]')).toHaveCount(0)
})

test('an escaped feature offers reopen, not ship, and states the round', async ({ page }) => {
  await stubLedger(page, ledger({
    stage: { stage: 'escaped', blurb: 'shipped, and bugs have been blamed on it since',
             rounds: 1, claimed_at: '2026-09-01', open_defects: 1, escapes: 1 },
    verbs: ['reopen', 'abandon'],
    next_step: { kind: 'reopen', label: '1 blamed defect — reopen to fix', target: '/features/242' },
  }))
  let posted = 0
  await page.route(/\/api\/features\/242\/reopen$/, (route) => {
    posted += 1
    return route.fulfill({ json: ledger({
      stage: { stage: 'building', blurb: 'being built', rounds: 2,
               claimed_at: null, open_defects: 1, escapes: 1 },
    }) })
  })

  await page.goto('/features/242')
  await expect(page.getByTestId('ship-feature')).toHaveCount(0)
  await expect(page.getByTestId('feature-next-step'))
    .toHaveText('Next: 1 blamed defect — reopen to fix')

  await page.getByTestId('reopen-feature').click()
  await expect(page.getByText(/this counts \+1 round/)).toBeVisible()
  await page.getByRole('button', { name: 'Reopen', exact: true }).last().click()

  await expect.poll(() => posted).toBe(1)
  await expect(page.locator('.stage-counts')).toContainText('2 rounds')
})

test('the title renames in place', async ({ page }) => {
  await stubLedger(page, ledger())
  let sent = null
  await page.route(/\/api\/features\/242$/, (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback()
    sent = route.request().postDataJSON()
    return route.fulfill({ json: ledger({ title: 'Renamed' }) })
  })

  await page.goto('/features/242')
  await page.getByTestId('feature-title').click()
  const input = page.getByTestId('feature-title-input')
  await input.fill('Renamed')
  await input.press('Enter')

  await expect(page.getByTestId('feature-title')).toHaveText('Renamed')
  expect(sent).toEqual({ title: 'Renamed' })
})

test('unattribute asks before withdrawing', async ({ page }) => {
  await stubLedger(page, ledger({
    sessions: {
      rows: [{
        trace_id: 'abcdef0123456789', short_trace: 'abcdef01',
        date: '2026-09-01', title: 'declared by hand',
        cost_usd: 1, provenance: 'recalled', round: null, is_primary: true,
        task_id: null,
      }],
      gaps: { traceless_runs: 0 },
    },
  }))
  let deleted = 0
  await page.route(/\/api\/features\/242\/attribute\//, (route) => {
    deleted += 1
    return route.fulfill({ json: { unattributed: true } })
  })

  await page.goto('/features/242')
  await page.locator('.ev-undeclare').click({ force: true })
  await expect(page.getByText(/Withdraw abcdef01 from this feature\?/)).toBeVisible()
  await page.getByRole('button', { name: 'Cancel' }).click()
  expect(deleted).toBe(0)
})

test('Build steps aside once the feature has slices', async ({ page }) => {
  await stubLedger(page, ledger({ slice_tasks: 3 }))
  await page.route('**/api/tasks?**', (route) =>
    route.fulfill({ json: { items: [], total: 0 } }))
  await page.goto('/features/242')
  await expect(page.getByTestId('feature-verb-spec')).toBeVisible()
  await expect(page.getByTestId('feature-verb-build')).toHaveCount(0)
})
