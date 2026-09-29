/**
 * Taking a feature down, and refusing a write without destroying the page.
 *
 * `POST /api/features/:id/abandon` has existed since the ledger did and
 * nothing in `frontend/src` called it, so the fifth stage was reachable from
 * the CLI alone — which meant a feature somebody had dropped kept reading as
 * one still in flight on every surface a browser can see.
 *
 * The second half of this file is the ship defect. `api.post` reports a
 * refusal by RETURNING `{ ok: false, msg }`, and the old `ship()` assigned
 * that envelope straight into `ledger` — so a refused ship replaced the whole
 * page with an error object.
 */
import { test, expect } from './auth-fixture.js'

function ledger(overrides = {}) {
  return {
    id: 242, title: 'Feature ledger', origin: 'native',
    external_ref: null, external_url: null, sync_error: null,
    stage: {
      stage: 'building', blurb: 'being built',
      rounds: 2, claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true,
    cost: { usd: null, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} },
    tokens: { input: 0, output: 0 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0,
                declared_usd: 0, all_usd: 0 },
    verbs: ['ship', 'abandon'],
    ship_check: { allowed: true, open_tasks: [], open_defects: [], warnings: [] },
    next_step: null,
    slice_tasks: 0,
    ...overrides,
  }
}

async function confirmShip(page) {
  await page.getByRole('button', { name: 'Ship', exact: true }).last().click()
}

async function stubLedger(page, body = ledger()) {
  await page.route(/\/api\/features\/242$/, (route) =>
    route.fulfill({ json: body }))
  // The ledger's Slices panel reads its own endpoint; left to the e2e server
  // it answers "no feature 242" as a second role=alert.
  await page.route(/\/api\/features\/242\/slices$/, (route) =>
    route.fulfill({ json: { rows: [], sliceable: false, tail_missing: false } }))
}

test('test_abandon_is_reachable_from_the_ui', async ({ page }) => {
  await stubLedger(page)
  let posted = 0
  await page.route(/\/api\/features\/242\/abandon$/, (route) => {
    posted += 1
    return route.fulfill({
      json: ledger({ stage: { stage: 'abandoned',
                              blurb: 'taken down or dropped — not a walked-back completion',
                              rounds: 2, claimed_at: null,
                              open_defects: 0, escapes: 0 },
                      verbs: ['reopen'] }),
    })
  })

  await page.goto('/features/242')

  const abandon = page.getByTestId('abandon-feature')
  await expect(abandon).toBeVisible()
  await abandon.click()

  // Confirmed, never fired on the first click: it is the one act on this page
  // that ends a feature's life.
  await expect(page.getByText('Take "Feature ledger" down?')).toBeVisible()
  expect(posted).toBe(0)

  await page.getByRole('button', { name: 'Abandon', exact: true }).last().click()

  await expect.poll(() => posted).toBe(1)
  // The response is the new ledger, so the stage the page renders is the one
  // the write produced — not a refetch that could disagree with it.
  await expect(page.locator('.stage-line')).toContainText('abandoned')
})

test('cancelling the confirm writes nothing', async ({ page }) => {
  await stubLedger(page)
  let posted = 0
  await page.route(/\/api\/features\/242\/abandon$/, (route) => {
    posted += 1
    return route.fulfill({ json: ledger() })
  })

  await page.goto('/features/242')
  await page.getByTestId('abandon-feature').click()
  await page.getByRole('button', { name: 'Cancel' }).click()

  await expect(page.getByTestId('abandon-feature')).toBeVisible()
  expect(posted).toBe(0)
})

test('an already-abandoned feature offers no second abandon', async ({ page }) => {
  await stubLedger(page, ledger({
    stage: { stage: 'abandoned', blurb: 'taken down or dropped',
             rounds: 2, claimed_at: null, open_defects: 0, escapes: 0 },
    verbs: ['reopen'],
  }))

  await page.goto('/features/242')

  await expect(page.locator('.stage-line')).toContainText('abandoned')
  await expect(page.getByTestId('abandon-feature')).toHaveCount(0)
})

test('a refused ship names the refusal and leaves the page standing',
  async ({ page }) => {
    await stubLedger(page)
    await page.route(/\/api\/features\/242\/ship$/, (route) =>
      route.fulfill({ status: 404, json: { error: 'no feature 242' } }))

    await page.goto('/features/242')
    await page.getByTestId('ship-feature').click()
    await confirmShip(page)

    await expect(page.getByRole('alert')).toContainText('no feature 242')
    // The page that fired the write is still the page: every figure below the
    // stage line used to read `undefined` off the error envelope.
    await expect(page.locator('.stage-key')).toContainText('Feature ledger')
    await expect(page.getByTestId('figure-cost')).toBeVisible()
  })

test('a ship in flight disables the row it was fired from', async ({ page }) => {
  await stubLedger(page)
  let release
  const held = new Promise((resolve) => { release = resolve })
  await page.route(/\/api\/features\/242\/ship$/, async (route) => {
    await held
    return route.fulfill({ json: ledger() })
  })

  await page.goto('/features/242')
  await page.getByTestId('ship-feature').click()
  await confirmShip(page)

  // An append-only log takes a second event from a second click, so the
  // control has to be shut for the duration of the first.
  await expect(page.getByTestId('ship-feature')).toBeDisabled()
  await expect(page.getByTestId('abandon-feature')).toBeDisabled()
  release()
})

// `api.post` REFUSES by returning `{ok:false}` and FAILS by throwing. Only the
// first was handled, so an unreachable server left both write controls
// disabled forever with nothing on screen to say why.
test('a write that cannot be sent frees the controls and says so',
  async ({ page }) => {
    await stubLedger(page)
    await page.route(/\/api\/features\/242\/ship$/, (route) => route.abort())

    await page.goto('/features/242')
    await page.getByTestId('ship-feature').click()
    await confirmShip(page)

    await expect(page.getByRole('alert')).toContainText('could not be sent')
    await expect(page.getByTestId('ship-feature')).toBeEnabled()
    await expect(page.getByTestId('abandon-feature')).toBeEnabled()
  })
