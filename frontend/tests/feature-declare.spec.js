/**
 * Declaring a feature from the population page.
 *
 * The page said "Declaring one is the only way in" and offered no way to do
 * it: the endpoint existed, the control did not, so the act a browser could
 * actually perform was capturing a task — which is the wrong end of the model.
 *
 * Stubbed, like the index spec beside it: every assertion here is about the
 * request the control sends and where it lands afterwards.
 */
import { test, expect } from './auth-fixture.js'

// The declare dialog's Spec field is filled as a textarea: the rich face is
// `f178-dialogs.spec.js`'s to test.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('regin_editor_mode', 'source')
    localStorage.setItem('regin_editor_mode:compose', 'source')
  })
})

function ledger(over = {}) {
  return {
    id: 300, title: 'declared from the browser', origin: 'native',
    external_ref: null, external_url: null, sync_error: null,
    stage: { stage: 'spec', blurb: 'specified, not started', rounds: 1,
             claimed_at: null, open_defects: 0, escapes: 0 },
    cost: { usd: null, gaps: { no_usage_captured: 0 } },
    work_time: {}, tokens: {},
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0 },
    ...over,
  }
}

async function stubIndex(page) {
  await page.route(/\/api\/features(\?.*)?$/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      json: {
        items: [], total: 0,
        pagination: { strategy: 'offset', total: 0, page: 0, size: 50,
                      has_next: false, has_prev: false },
        coverage: { declared_sessions: 0, all_sessions: 0,
                    declared_usd: 0, all_usd: 0 },
        unattributed_defects: 0,
      },
    })
  })
}

test('the index carries the control that declares a feature',
  async ({ page }) => {
    await stubIndex(page)
    let sent = null
    await page.route(/\/api\/features$/, (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      sent = route.request().postDataJSON()
      return route.fulfill({ status: 201, json: ledger() })
    })

    await page.goto('/features')
    await page.getByTestId('declare-feature').click()
    await page.getByTestId('declare-title').fill('declared from the browser')
    await page.getByTestId('declare-body').fill('## Intent\nthin prose')
    await page.getByTestId('declare-submit').click()

    await expect.poll(() => sent).not.toBeNull()
    expect(sent.title).toBe('declared from the browser')
    expect(sent.body).toContain('thin prose')
  })

test('declaring from elsewhere lands on the new feature page', async ({ page }) => {
  await stubIndex(page)
  await page.route(/\/api\/features$/, (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    return route.fulfill({ status: 201, json: ledger() })
  })
  await page.route(/\/api\/features\/300$/, (route) =>
    route.fulfill({ json: { ...ledger(), stage_live: true, synced_at: null } }))

  // Declared from the Features list it stays in the list (R5.4,
  // f155-list-order.spec.js); from another tab the list is not on screen.
  await page.goto('/tasks')
  await page.getByTestId('pipeline-new').click()
  await page.getByTestId('pipeline-new-feature').click()
  await page.getByTestId('declare-title').fill('declared from the browser')
  await page.getByTestId('declare-submit').click()

  await expect(page).toHaveURL(/\/features\/300$/)
  // The ledger page carries the title in its stage line, not in a heading.
  await expect(page.locator('.stage-key'))
    .toContainText('declared from the browser')
})

test('a title the server refuses is named, and the dialog stays open',
  async ({ page }) => {
    await stubIndex(page)
    await page.route(/\/api\/features$/, (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({ status: 400,
                             json: { error: 'a feature needs a title' } })
    })

    await page.goto('/features')
    await page.getByTestId('declare-feature').click()
    await page.getByTestId('declare-title').fill('   ')
    await page.getByTestId('declare-submit').click()

    await expect(page.getByRole('alert')).toContainText('needs a title')
    await expect(page.getByTestId('declare-title')).toBeVisible()
  })

test('a viewer sees no declare control', async ({ page }) => {
  await stubIndex(page)
  await page.addInitScript(() => {
    const raw = localStorage.getItem('regin_auth_user')
    if (raw) {
      const user = JSON.parse(raw)
      user.role = 'viewer'
      localStorage.setItem('regin_auth_user', JSON.stringify(user))
    }
  })

  await page.goto('/features')

  await expect(page.getByTestId('declare-feature')).toHaveCount(0)
})

// `api_feature_create` has accepted `ref` since it was written, and nothing in
// frontend/src reached it: a tracker issue could only be adopted from the CLI.
test('a tracker issue can be adopted from the Features tab', async ({ page }) => {
  await stubIndex(page)
  let sent = null
  await page.route(/\/api\/features$/, (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    sent = route.request().postDataJSON()
    return route.fulfill({
      status: 201,
      json: ledger({ origin: 'linear', external_ref: 'CAI-274' }),
    })
  })

  await page.goto('/features')
  await page.getByTestId('declare-feature').click()
  await page.getByTestId('mode-adopt').click()

  // One input means one thing per mode: a title to write, or a reference to
  // resolve — never a field that is both.
  await expect(page.getByTestId('declare-title')).toHaveCount(0)
  await page.getByTestId('adopt-ref').fill('CAI-274')
  await page.getByTestId('declare-submit').click()

  await expect.poll(() => sent).not.toBeNull()
  expect(sent.ref).toBe('CAI-274')
  expect(sent.title).toBeUndefined()
})

test('a reference the tracker refuses is named, and the dialog stays open',
  async ({ page }) => {
    await stubIndex(page)
    await page.route(/\/api\/features$/, (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      return route.fulfill({
        status: 400, json: { error: 'no issue CAI-999 on linear' } })
    })

    await page.goto('/features')
    await page.getByTestId('declare-feature').click()
    await page.getByTestId('mode-adopt').click()
    await page.getByTestId('adopt-ref').fill('CAI-999')
    await page.getByTestId('declare-submit').click()

    await expect(page.getByRole('alert')).toContainText('no issue CAI-999')
    await expect(page.getByTestId('adopt-ref')).toBeVisible()
  })
