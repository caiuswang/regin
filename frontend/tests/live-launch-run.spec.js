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

test('a promptless launch is allowed — except as a one-shot', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)

  await openSheet(page, traceId)

  // Nothing typed is a launch, the way bare `claude` is at a terminal: the
  // session comes up and the first turn arrives from the card's composer.
  await expect(page.getByTestId('live-launch-go')).toBeEnabled()
  await page.getByTestId('live-launch-oneshot').click()
  // A run that ends with its first turn, given no turn, would connect and
  // immediately disconnect — refused here so the reason precedes the click.
  await expect(page.getByTestId('live-launch-oneshot-hint')).toBeVisible()
  await expect(page.getByTestId('live-launch-go')).toBeDisabled()
  await page.getByTestId('live-launch-prompt').fill('audit the trace merge')
  await expect(page.getByTestId('live-launch-go')).toBeEnabled()
})

test('a promptless launch posts no prompt and opens the run', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  let body = null
  await page.route('**/api/agent-runs', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { launched: true, trace_id: 'sdk-abc123def456' } })
  })

  await openSheet(page, traceId)
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  expect(body.prompt).toBe('')
  expect(body.resume).toBeUndefined()
  await expect(page).toHaveURL(/\/live\/sdk-abc123def456$/)
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

// ── continuing a session ──────────────────────────────────────────────
//
// The sheet no longer asks whether the session in view is resumable; it lists
// what /api/agent-runs/resumable says can be continued, so these tests drive
// that list. Which rows qualify is the server's rule and is pinned in
// tests/agent_sdk/test_resumable_list.py.

// A stopped run regin launched: the picker names the child session, and carries
// the run's own `sdk-…` id so the client can recognise the trace it is on.
const RESUMABLE_RUN = 'sdk-resumable01'

const RUN_ROW = {
  session_id: 'child-session-1', title: 'a launched run',
  cwd: '/Users/dev/repo-a', last_seen: '2026-01-01T11:00:00', prompts: 5,
  kind: 'run', run_trace_id: RESUMABLE_RUN,
}
// A session the operator drove in a terminal, in a directory the install has
// not registered as a repo.
const TERMINAL_ROW = {
  session_id: 'terminal-session-9', title: 'a terminal session',
  cwd: '/Users/dev/worktree-x', last_seen: '2026-01-01T10:00:00', prompts: 3,
  kind: 'session', run_trace_id: '',
}

/** Serve the picker's list. Registered after any `**\/api/agent-runs` mock so
 *  the more specific pattern wins. Records the query it was asked for. */
async function mockResumable(page, rows, seen = {}) {
  await page.route('**/api/agent-runs/resumable*', (route) => {
    const q = new URL(route.request().url()).searchParams.get('q') || ''
    seen.q = q
    const list = q
      ? rows.filter(r => r.title.includes(q) || r.cwd.includes(q))
      : rows
    return route.fulfill({ json: { sessions: list } })
  })
  return seen
}

async function openPicker(page, traceId) {
  await openSheet(page, traceId)
  await page.getByTestId('live-launch-resume-open').click()
  await settle(page)
}

test('the picker lists what can be continued and says what each pick does to the trace',
  async ({ page }) => {
    const traceId = await postSession(page)
    await mockOptions(page)
    await mockResumable(page, [RUN_ROW, TERMINAL_ROW])

    await openPicker(page, traceId)

    await expect(page.getByTestId('live-resume-row')).toHaveCount(2)
    // The distinction an operator can only act on before picking: one keeps the
    // conversation on its trace, the other starts a second one. Each row names
    // what the session *is* as well as what picking it does — the effect alone
    // has no referent in a list of sessions unrelated to the card.
    await expect(page.getByTestId('live-resume-row').first())
      .toContainText('regin run · keeps its trace')
    await expect(page.getByTestId('live-resume-row').nth(1))
      .toContainText('terminal session · new trace')
  })

test('picking a session sends its id and adopts its working directory', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  let body = null
  await page.route('**/api/agent-runs', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { launched: true, trace_id: 'sdk-new01' } })
  })
  await mockResumable(page, [TERMINAL_ROW])

  await openPicker(page, traceId)
  await page.getByTestId('live-resume-row').click()
  await settle(page)
  await page.getByTestId('live-launch-prompt').fill('pick it back up')
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  expect(body.resume).toBe(TERMINAL_ROW.session_id)
  // Not a convenience: `claude --resume` resolves the id relative to the
  // working directory, so launching from anywhere else continues nothing —
  // including from a path the launch options never offered.
  expect(body.cwd).toBe(TERMINAL_ROW.cwd)
})

test('a picked session launches with no prompt, and adopts the model it ran on',
  async ({ page }) => {
    const traceId = await postSession(page)
    await mockOptions(page)
    let body = null
    await page.route('**/api/agent-runs', async (route) => {
      body = route.request().postDataJSON()
      await route.fulfill({ json: { launched: true, trace_id: 'sdk-new01' } })
    })
    await mockResumable(page, [{ ...RUN_ROW, model: 'claude-opus-5' }])

    await openPicker(page, traceId)
    await page.getByTestId('live-resume-row').click()
    await settle(page)
    // No prompt typed: reopening the conversation is the act, and the card it
    // lands on has a composer for whatever comes next.
    await page.getByTestId('live-launch-go').click()
    await settle(page)

    expect(body.resume).toBe(RUN_ROW.session_id)
    // Continuing on a different model than the half being continued is a
    // change nobody asked for and nothing on the card would show.
    expect(body.model).toBe('claude-opus-5')
  })

test('searching asks the server rather than filtering the page it already has',
  async ({ page }) => {
    const traceId = await postSession(page)
    await mockOptions(page)
    const seen = await mockResumable(page, [RUN_ROW, TERMINAL_ROW])

    await openPicker(page, traceId)
    await page.getByTestId('live-resume-search').fill('worktree-x')
    await settle(page)

    // Server-side, so the search reaches sessions past the first page.
    expect(seen.q).toBe('worktree-x')
    await expect(page.getByTestId('live-resume-row')).toHaveCount(1)
    await expect(page.getByTestId('live-resume-row')).toContainText('a terminal session')
  })

test('a picked session can be dropped again before launching', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  let body = null
  await page.route('**/api/agent-runs', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { launched: true, trace_id: 'sdk-new01' } })
  })
  await mockResumable(page, [TERMINAL_ROW])

  await openPicker(page, traceId)
  await page.getByTestId('live-resume-row').click()
  await settle(page)
  await page.getByTestId('live-launch-resume-clear').click()
  await page.getByTestId('live-launch-prompt').fill('start fresh after all')
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  expect(body.resume).toBeUndefined()
})

test('resuming the session already open reloads the card instead of navigating away',
  async ({ page }) => {
    // The id the session list offers — the `sdk-…` half is hidden — so this is
    // the id `/live` is normally open on. Relaunching returns the run's OWN id,
    // which renders the same merged trace; moving the card to it would be a
    // navigation with nothing behind it.
    const child = await postSession(page)
    await mockOptions(page)
    await page.route('**/api/agent-runs', (route) =>
      route.fulfill({ json: { launched: true, trace_id: RESUMABLE_RUN } }))
    await mockResumable(page, [{ ...RUN_ROW, session_id: child }])
    // The session-row fetch `start()` opens with. The route does not change on
    // a resume, so nothing but an explicit re-init can produce a second one.
    let reloads = 0
    page.on('request', (r) => { if (/\/api\/sessions\?/.test(r.url())) reloads += 1 })

    await openPicker(page, child)
    const before = reloads
    await page.getByTestId('live-resume-row').click()
    await settle(page)
    await page.getByTestId('live-launch-prompt').fill('pick it back up')
    await page.getByTestId('live-launch-go').click()
    await settle(page)

    expect(reloads).toBeGreaterThan(before)
    await expect(page).toHaveURL(new RegExp(`/live/${child}$`))
  })

test('nothing resumable says so instead of rendering a dead list', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  await mockResumable(page, [])

  await openPicker(page, traceId)

  await expect(page.getByTestId('live-resume-empty')).toBeVisible()
  await expect(page.getByTestId('live-resume-row')).toHaveCount(0)
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

// ── the draft outlives the sheet ──────────────────────────────────────
// The sheet's slot is a v-if, so dismissing it unmounts the form. Backing out
// of the picker by tapping the backdrop (Escape here) is the natural gesture
// for "never mind" — and it used to take the typed prompt and the picked
// session with it, with no way to get either back.

test('dismissing the sheet mid-pick keeps what was already typed', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  await mockResumable(page, [TERMINAL_ROW])

  await openSheet(page, traceId)
  await page.getByTestId('live-launch-prompt').fill('half-written instruction')
  await page.getByTestId('live-launch-model').fill('claude-sonnet-4')
  await page.getByTestId('live-launch-resume-open').click()
  await settle(page)
  await page.keyboard.press('Escape')
  await settle(page)
  await page.getByTestId('live-launch-btn').click()
  await settle(page)

  // Reopening lands on the form, not back inside the picker being left.
  await expect(page.getByTestId('live-launch-prompt'))
    .toHaveValue('half-written instruction')
  await expect(page.getByTestId('live-launch-model')).toHaveValue('claude-sonnet-4')
})

test('a session picked before the sheet was dismissed is still picked', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  let body = null
  await page.route('**/api/agent-runs', async (route) => {
    body = route.request().postDataJSON()
    await route.fulfill({ json: { launched: true, trace_id: 'sdk-new01' } })
  })
  await mockResumable(page, [TERMINAL_ROW])

  await openPicker(page, traceId)
  await page.getByTestId('live-resume-row').click()
  await settle(page)
  await page.getByTestId('live-launch-prompt').fill('carry on')
  await page.keyboard.press('Escape')
  await settle(page)
  await page.getByTestId('live-launch-btn').click()
  await settle(page)

  await expect(page.getByTestId('live-launch-resume-label'))
    .toContainText(TERMINAL_ROW.title)
  await page.getByTestId('live-launch-go').click()
  await settle(page)
  expect(body.resume).toBe(TERMINAL_ROW.session_id)
  expect(body.prompt).toBe('carry on')
})

test('a launch that started clears the draft for the next one', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  await page.route('**/api/agent-runs', (route) =>
    route.fulfill({ json: { launched: true, trace_id: traceId } }))

  await openSheet(page, traceId)
  await page.getByTestId('live-launch-prompt').fill('already sent')
  await page.getByTestId('live-launch-go').click()
  await settle(page)
  await page.getByTestId('live-launch-btn').click()
  await settle(page)

  await expect(page.getByTestId('live-launch-prompt')).toHaveValue('')
})

test('a refused launch keeps the prompt so it can be retried', async ({ page }) => {
  const traceId = await postSession(page)
  await mockOptions(page)
  await page.route('**/api/agent-runs', (route) =>
    route.fulfill({ json: { launched: false, detail: 'at capacity' } }))

  await openSheet(page, traceId)
  await page.getByTestId('live-launch-prompt').fill('worth retrying')
  await page.getByTestId('live-launch-go').click()
  await settle(page)

  await expect(page.getByTestId('live-launch-refusal')).toContainText('at capacity')
  await expect(page.getByTestId('live-launch-prompt')).toHaveValue('worth retrying')
})

test('the draft does not follow the operator onto another session\'s card',
  async ({ page }) => {
    const cardA = await postSession(page)
    const cardB = await postSession(page)
    await mockOptions(page)
    await mockResumable(page, [TERMINAL_ROW])

    await openPicker(page, cardA)
    await page.getByTestId('live-resume-row').click()
    await settle(page)
    await page.getByTestId('live-launch-prompt').fill('meant for card A')
    await page.keyboard.press('Escape')
    await settle(page)

    // Walked to in-app (the switch sheet), not reloaded: a full page load
    // would clear the draft anyway and prove nothing. A pick made on card A is
    // one Launch away from continuing a session the operator never chose here,
    // in a working directory they never chose either.
    // The switcher lists `kind=real` only, which excludes the span fixtures
    // above — served here so the walk lands on a known card.
    await page.route(/\/api\/sessions\?kind=real/, (route) =>
      route.fulfill({ json: { sessions: [
        { trace_id: cardB, title: 'card B', last_seen: '2026-01-01T10:00:00',
          status: 'ended', span_count: 1 },
      ] } }))
    await page.getByTestId('live-switch').click()
    await settle(page)
    await page.locator(`[data-testid="live-picker-row"][data-trace-id="${cardB}"]`).click()
    await settle(page)
    await expect(page).toHaveURL(new RegExp(`/live/${cardB}$`))
    await page.getByTestId('live-launch-btn').click()
    await settle(page)

    await expect(page.getByTestId('live-launch-prompt')).toHaveValue('')
    await expect(page.getByTestId('live-launch-resumed')).toHaveCount(0)
    await expect(page.getByTestId('live-launch-resume-open')).toBeVisible()
  })

test('a draft typed before the session id resolves is not wiped when it lands',
  async ({ page }) => {
    const traceId = await postSession(page)
    await mockOptions(page)
    // The view resolves its session row asynchronously and the launch button
    // is live before that lands, so the sheet's first mount sees an empty id.
    // Treating that as "a different card" would discard the draft the instant
    // the real id arrived — the same loss, by a different route.
    let release = null
    await page.route(/\/api\/sessions\?trace_id=/, async (route) => {
      await new Promise((r) => { release = r })
      await route.continue()
    })
    await page.goto(`/live/${traceId}`)
    await page.getByTestId('live-launch-btn').click()
    await page.getByTestId('live-launch-prompt').fill('typed while resolving')
    await page.keyboard.press('Escape')

    release()
    await settle(page)
    await page.getByTestId('live-launch-btn').click()
    await settle(page)

    await expect(page.getByTestId('live-launch-prompt'))
      .toHaveValue('typed while resolving')
  })

test('dropping a pick gives back the working directory it overwrote',
  async ({ page }) => {
    const traceId = await postSession(page)
    await mockOptions(page)
    let body = null
    await page.route('**/api/agent-runs', async (route) => {
      body = route.request().postDataJSON()
      await route.fulfill({ json: { launched: true, trace_id: 'sdk-new01' } })
    })
    await mockResumable(page, [TERMINAL_ROW])

    await openSheet(page, traceId)
    await page.getByTestId('live-launch-cwd').click()
    await page.getByRole('option', { name: OPTIONS.cwds[0] }).click()
    await page.getByTestId('live-launch-resume-open').click()
    await settle(page)
    await page.getByTestId('live-resume-row').click()
    await settle(page)
    await page.getByTestId('live-launch-resume-clear').click()
    await page.getByTestId('live-launch-go').click()
    await settle(page)

    // Not TERMINAL_ROW.cwd: that directory belonged to the session that was
    // dropped, and left behind it silently becomes the next launch's.
    expect(body.cwd).toBe(OPTIONS.cwds[0])
  })
