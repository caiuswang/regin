/**
 * Launch a new agent run from the /live card
 * (`frontend/src/components/live/LiveLaunchSheet.vue`).
 *
 * The SDK tier's launch route existed with no client at all — this sheet is it.
 * These tests pin the BROWSER-side contract only: what the form offers (which
 * comes from `/api/agent-runs/launch-options`, not from hardcoded lists), the
 * exact POST body that reaches `/api/agent-runs`, and the two outcomes that
 * must NOT silently navigate — a refusal, and a run whose permission mode made
 * gating inert. The server side is owned by tests/agent_sdk/test_launch_route.py.
 *
 * Conventions mirror live-picker.spec.js: synthetic `is_test: true` session
 * posted via /api/session-spans so the card has a tail to render, pinned
 * 375x667 viewport, `settle()` between interactions.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { settle } from './helpers/overflow.js'

test.use({ viewport: { width: 375, height: 667 } })

const OPTIONS = {
  enabled: true,
  cwds: ['/Users/dev/repo-a', '/Users/dev/repo-b'],
  permission_modes: ['default', 'acceptEdits', 'plan', 'bypassPermissions'],
  default_permission_mode: 'default',
  default_model: '',
  gating_active: true,
}

async function postSession(page) {
  const traceId = randomUUID()
  const now = new Date().toISOString()
  const sfx = traceId.slice(0, 8)
  const res = await page.request.post('/api/session-spans', {
    data: [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: `LAUNCH_FIXTURE_${sfx}`, is_test: true } },
    ],
  })
  expect(res.ok()).toBeTruthy()
  return traceId
}

async function mockOptions(page, overrides = {}) {
  await page.route('**/api/agent-runs/launch-options', (route) =>
    route.fulfill({ json: { ...OPTIONS, ...overrides } }))
}

async function openSheet(page, traceId) {
  await page.goto(`/live/${traceId}`)
  await settle(page)
  await page.getByTestId('live-launch-btn').click()
  await settle(page)
}

test('a disabled tier explains itself instead of offering a dead form', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page, { enabled: false })

  await openSheet(page, traceId)

  await expect(page.getByTestId('live-launch-disabled')).toBeVisible()
  await expect(page.getByTestId('live-launch-go')).toHaveCount(0)
})

test('the form offers exactly the working directories the server named', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)

  await openSheet(page, traceId)

  await page.getByTestId('live-launch-cwd').click()
  await settle(page)
  // The two repos plus the "server working directory" default — a client that
  // hardcoded a list would drift from the install it is driving.
  await expect(page.getByRole('option')).toHaveCount(3)
  await expect(page.getByRole('option', { name: '/Users/dev/repo-a' })).toBeVisible()
  await page.keyboard.press('Escape')
})

test('launch is refused until there is a prompt', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)

  await openSheet(page, traceId)

  await expect(page.getByTestId('live-launch-go')).toBeDisabled()
  await page.getByTestId('live-launch-prompt').fill('   ')
  await expect(page.getByTestId('live-launch-go')).toBeDisabled()
  await page.getByTestId('live-launch-prompt').fill('audit the trace merge')
  await expect(page.getByTestId('live-launch-go')).toBeEnabled()
})

test('the chosen overrides reach the launch route, then it opens the run', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  let body = null
  await page.route('**/api/agent-runs', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { launched: true, trace_id: 'sdk-abc123def456' } })
  })

  await openSheet(page, traceId)
  await page.getByTestId('live-launch-prompt').fill('audit the trace merge')
  await page.getByTestId('live-launch-cwd').click()
  await page.getByRole('option', { name: '/Users/dev/repo-b' }).click()
  await page.getByTestId('live-launch-model').fill('claude-opus-5')
  await page.getByTestId('live-launch-oneshot').click()
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  expect(body.prompt).toBe('audit the trace merge')
  expect(body.cwd).toBe('/Users/dev/repo-b')
  expect(body.model).toBe('claude-opus-5')
  expect(body.one_shot).toBe(true)
  // A launched run exists before it has done anything, so the card follows it.
  await expect(page).toHaveURL(/\/live\/sdk-abc123def456$/)
})

test('a refusal stays on the form and says why', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  await page.route('**/api/agent-runs', (route) =>
    route.fulfill({ json: { launched: false, detail: 'max_concurrent_runs reached' } }))

  await openSheet(page, traceId)
  await page.getByTestId('live-launch-prompt').fill('one more')
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  await expect(page.getByTestId('live-launch-refusal'))
    .toContainText('max_concurrent_runs reached')
  await expect(page).toHaveURL(new RegExp(`/live/${traceId}$`))
})

test('a mode that makes gating inert is shown before the card is left', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  await page.route('**/api/agent-runs', (route) => route.fulfill({
    json: {
      launched: true, trace_id: 'sdk-shadowed01',
      warning: "permission_mode='acceptEdits' skips the permission callback",
    },
  }))

  await openSheet(page, traceId)
  await page.getByTestId('live-launch-prompt').fill('go')
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  // Held deliberately: a security control that quietly does nothing must not
  // be reported by a toast the operator scrolls past.
  await expect(page.getByTestId('live-launch-held')).toContainText('acceptEdits')
  await expect(page).toHaveURL(new RegExp(`/live/${traceId}$`))

  await page.getByTestId('live-launch-open').click()
  await expect(page).toHaveURL(/\/live\/sdk-shadowed01$/)
})

test('picking a shadowing mode warns before launching, not after', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)

  await openSheet(page, traceId)
  await page.getByTestId('live-launch-mode').click()
  await page.getByRole('option', { name: 'bypassPermissions' }).click()
  await settle(page)

  await expect(page.getByTestId('live-launch-shadow')).toBeVisible()
})

test('resume is offered for a terminal session and withheld for an unrecorded sdk one', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)

  await openSheet(page, traceId)
  // A session the user drove: its trace id IS the CLI's session id.
  await expect(page.getByTestId('live-launch-resume')).toBeVisible()
  await expect(page.getByTestId('live-launch-resume-label'))
    .toContainText('opens a new trace')

  // An `sdk-…` id regin has no run row for names nothing the CLI can reopen,
  // so the option must not be offered at all.
  await page.goto('/live/sdk-abc123def456')
  await settle(page)
  await page.getByTestId('live-launch-btn').click()
  await settle(page)
  await expect(page.getByTestId('live-launch-resume')).toHaveCount(0)
})

// A stopped run regin launched, as the server reports it: the child named
// itself, so the conversation behind it can be reopened.
const RESUMABLE_RUN = 'sdk-resumable01'

async function mockRun(page, body) {
  await page.route('**/api/agent-runs/sdk-*', (route) =>
    route.fulfill({ json: { trace_id: RESUMABLE_RUN, status: 'exited', owned: false, ...body } }))
}

test('a stopped run regin launched is continued in the trace already open', async ({ page }) => {
  await mockOptions(page)
  await mockRun(page, { cli_session_id: 'child-session-1', resumable: true })
  let body = null
  await page.route('**/api/agent-runs', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { launched: true, trace_id: RESUMABLE_RUN } })
  })

  await page.goto(`/live/${RESUMABLE_RUN}`)
  await settle(page)
  await page.getByTestId('live-launch-btn').click()
  await settle(page)

  await expect(page.getByTestId('live-launch-resume-label')).toContainText('same trace')
  await page.getByTestId('live-launch-prompt').fill('pick it back up')
  await page.getByTestId('live-launch-resume').click()
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  // The run's own id is what the server is asked to resume — it resolves the
  // CLI session behind it, which this client has no business knowing.
  expect(body.resume).toBe(RESUMABLE_RUN)
  await expect(page).toHaveURL(new RegExp(`/live/${RESUMABLE_RUN}$`))
})

test('resuming into the open trace reloads the card instead of sitting on the ended tail',
  async ({ page }) => {
    await mockOptions(page)
    await mockRun(page, { cli_session_id: 'child-session-1', resumable: true })
    await page.route('**/api/agent-runs', (route) =>
      route.fulfill({ json: { launched: true, trace_id: RESUMABLE_RUN } }))
    // The session-row fetch `start()` opens with. The route does not change on
    // a resume, so nothing but an explicit re-init can produce a second one.
    let reloads = 0
    page.on('request', (r) => { if (/\/api\/sessions\?/.test(r.url())) reloads += 1 })

    await page.goto(`/live/${RESUMABLE_RUN}`)
    await settle(page)
    await page.getByTestId('live-launch-btn').click()
    await settle(page)
    const before = reloads

    await page.getByTestId('live-launch-prompt').fill('pick it back up')
    await page.getByTestId('live-launch-resume').click()
    await page.getByTestId('live-launch-go').click()
    await settle(page)

    expect(reloads).toBeGreaterThan(before)
  })

test('a run reached by its child id is continued where the operator already is',
  async ({ page }) => {
    // The id the session list offers — the `sdk-…` half is hidden — so this is
    // the id `/live` is normally open on.
    const child = await postSession(page)
    await mockOptions(page)
    await page.route(`**/api/agent-runs/${child}`, (route) =>
      route.fulfill({
        json: {
          trace_id: RESUMABLE_RUN, status: 'exited', owned: false,
          cli_session_id: child, resumable: true,
        },
      }))
    let body = null
    await page.route('**/api/agent-runs', async (route) => {
      body = route.request().postDataJSON()
      await route.fulfill({ json: { launched: true, trace_id: RESUMABLE_RUN } })
    })

    await openSheet(page, child)

    await expect(page.getByTestId('live-launch-resume-label')).toContainText('same trace')
    await page.getByTestId('live-launch-prompt').fill('pick it back up')
    await page.getByTestId('live-launch-resume').click()
    await page.getByTestId('live-launch-go').click()
    await settle(page)

    expect(body.resume).toBe(child)
    // Relaunched under the run's own id, which renders the same merged trace —
    // moving the card to it would be a navigation with nothing behind it.
    await expect(page).toHaveURL(new RegExp(`/live/${child}$`))
  })

test('a run whose child never reported is not offered as resumable', async ({ page }) => {
  await mockOptions(page)
  await mockRun(page, { cli_session_id: null, resumable: false })

  await page.goto(`/live/${RESUMABLE_RUN}`)
  await settle(page)
  await page.getByTestId('live-launch-btn').click()
  await settle(page)

  // The server's answer, not a shape the client inferred: there is no
  // conversation behind this run to continue.
  await expect(page.getByTestId('live-launch-resume')).toHaveCount(0)
})

test('no shadow warning when the install gates nothing', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page, { gating_active: false })

  await openSheet(page, traceId)
  await page.getByTestId('live-launch-mode').click()
  await page.getByRole('option', { name: 'bypassPermissions' }).click()
  await settle(page)

  await expect(page.getByTestId('live-launch-shadow')).toHaveCount(0)
})
