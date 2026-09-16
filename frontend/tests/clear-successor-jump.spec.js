/**
 * `/clear` succession — the page follows the send (`useSuccessorJump`).
 *
 * `/clear` ends the conversation and opens another under a new id, in the
 * same tmux pane. `bridge-send` steers through to that one and names it as
 * `trace_id` in the response, so the page the prompt was typed on is no
 * longer where the answer will appear — it has to follow.
 *
 * Browser-side contract ONLY — what the page does with the `trace_id` the
 * send comes back with. The server half is pinned in Python, and has to be:
 * the Playwright server runs with agent_bridge DISABLED, so it can never
 * produce a real `bridge_reachable` or `successor_trace_id`, and the map patch
 * below is the same in-flight fake `live-card.spec.js` uses. Read that as a
 * split, not as coverage — `bridge_reachable: true` here is a premise, and the
 * test that it is actually TRUE for a cleared session is
 * `test_a_cleared_session_is_reachable_through_its_successor`
 * (tests/agent_bridge/test_web_proxy.py), which runs the real
 * `_bridge_reachability`.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { settle } from './helpers/overflow.js'

test.use({ viewport: { width: 375, height: 667 } })

// Every test here navigates the page off the session whose `/map` it stubbed,
// and a handler still mid-`route.fetch()` when the test ends fails on a
// context that is already gone. Dropping the routes first is what Playwright's
// own error for that race recommends.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function postSession(page, marker) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  await page.request.post('/api/session-spans', {
    data: [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null,
        name: 'prompt', start_time: now,
        attributes: { text: marker, is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null,
        name: 'assistant_response', start_time: now,
        attributes: { text: `${marker} answered`, is_test: true } },
    ],
  })
  return traceId
}

async function bridgeReachableMap(page, traceId, extra = {}) {
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const resp = await route.fetch()
    // Buffer the body before fulfilling and do NOT pass `response: resp`:
    // holding the fetched response open across the fulfill races its
    // disposal ("Response has been disposed") and makes this flaky.
    const json = JSON.parse(await resp.text())
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      json: {
        ...json,
        bridge_reachable: true,
        bridge_pane: '%3',
        last_seen: new Date(Date.now() - 30_000).toISOString(),
        phase: 'idle',
        agent_phase: { main: 'idle' },
        phase_config: { working_window_sec: 12, idle_settle_sec: 6,
                        inactive_threshold_sec: 600 },
        ...(typeof extra === 'function' ? extra() : extra),
      },
    })
  })
}

async function stubBridgeSend(page, traceId, body) {
  const posts = []
  await page.route(`**/api/sessions/${traceId}/bridge-send`, async (route) => {
    posts.push(route.request().postDataJSON())
    await route.fulfill({ json: { id: 1, delivered: true, detail: 'delivered to %3', ...body } })
  })
  return posts
}

const composerTa = (page) => page.locator('[data-testid="live-composer-ta"]')
const composerSend = (page) => page.locator('[data-testid="live-composer-send"]')

async function openIdleLive(page, traceId) {
  await page.goto(`/live/${traceId}`)
  await settle(page)
  await expect(page.locator('[data-testid="live-now"]'))
    .toHaveAttribute('data-state', 'idle', { timeout: 20_000 })
}

test('a send routed to the successor moves the page onto it', async ({ page }) => {
  const cleared = await postSession(page, 'CLEARED_SESSION_MARKER')
  const successor = await postSession(page, 'SUCCESSOR_SESSION_MARKER')
  await bridgeReachableMap(page, cleared)
  await bridgeReachableMap(page, successor)
  const posts = await stubBridgeSend(page, cleared, { trace_id: successor })

  await openIdleLive(page, cleared)
  await composerTa(page).fill('carry on')
  await composerSend(page).click()

  await expect(page).toHaveURL(new RegExp(`/live/${successor}$`), { timeout: 10_000 })
  expect(posts).toEqual([{ text: 'carry on' }])
  // The tail is the successor's, not the page we left — the jump re-inits
  // the view rather than renaming the session in place. Same budget as the
  // initial load below: this waits on a fresh poll cycle, and under a full
  // parallel run that server is answering seven other workers.
  await expect(page.getByText('SUCCESSOR_SESSION_MARKER').first())
    .toBeVisible({ timeout: 20_000 })
})

test('an ordinary send names its own session and stays put', async ({ page }) => {
  const traceId = await postSession(page, 'ORDINARY_SESSION_MARKER')
  await bridgeReachableMap(page, traceId)
  await stubBridgeSend(page, traceId, { trace_id: traceId })

  await openIdleLive(page, traceId)
  await composerTa(page).fill('keep going')
  await composerSend(page).click()

  await expect(page.locator('[data-testid="live-bridge-meta"]'))
    .toContainText('✓ delivered to %3', { timeout: 5_000 })
  await expect(page).toHaveURL(new RegExp(`/live/${traceId}$`))
})

test('a response with no trace_id leaves the page alone', async ({ page }) => {
  // The SDK tier answers without the field; a client that treated a missing
  // id as a destination would navigate to `/live/undefined`.
  const traceId = await postSession(page, 'NO_FIELD_SESSION_MARKER')
  await bridgeReachableMap(page, traceId)
  await stubBridgeSend(page, traceId, {})

  await openIdleLive(page, traceId)
  await composerTa(page).fill('still here')
  await composerSend(page).click()

  await expect(page.locator('[data-testid="live-bridge-meta"]'))
    .toContainText('✓ delivered to %3', { timeout: 5_000 })
  await expect(page).toHaveURL(new RegExp(`/live/${traceId}$`))
})


test('a successor appearing while the page is open moves it', async ({ page }) => {
  // The recovery leg of the pushed `session_successor` frame. The frame
  // itself cannot be exercised here — the Playwright server runs with
  // agent_bridge DISABLED and can never resolve a real succession, which is
  // why `succession.announce` is pinned in Python. What the browser owns is
  // what it does once the field arrives, on the stream or on the poll behind
  // it, and that path is shared: both legs end in the same `jumpTo`.
  //
  // The field must ARRIVE, not be there from the start: a page opened onto an
  // already-cleared session stays put (the test below), so serving the
  // successor immediately would prove nothing about either behavior.
  const cleared = await postSession(page, 'STRANDED_SESSION_MARKER')
  const successor = await postSession(page, 'HEIR_SESSION_MARKER')
  let heirVisible = false
  await bridgeReachableMap(page, cleared, () => (
    heirVisible ? { successor_trace_id: successor } : {}))
  await bridgeReachableMap(page, successor)

  await openIdleLive(page, cleared)
  // The `/clear` lands now, mid-session, exactly as it would in the terminal.
  heirVisible = true

  // No send, no click: the page has to follow on its own.
  await expect(page).toHaveURL(new RegExp(`/live/${successor}$`), { timeout: 20_000 })
  await expect(page.getByText('HEIR_SESSION_MARKER').first())
    .toBeVisible({ timeout: 20_000 })
})

test('a summary with no successor leaves the page alone', async ({ page }) => {
  const traceId = await postSession(page, 'SETTLED_SESSION_MARKER')
  await bridgeReachableMap(page, traceId)

  await openIdleLive(page, traceId)

  await expect(page).toHaveURL(new RegExp(`/live/${traceId}$`))
})


test('opening a cleared session directly does not bounce you off it', async ({ page }) => {
  // The recovery watcher must fire for a succession that happens WHILE the
  // operator is here, not for one the first load merely reports. Reading a
  // cleared session's trace on purpose is a legitimate thing to do, and a
  // `replace` would leave no history entry to come back from.
  const cleared = await postSession(page, 'DELIBERATE_SESSION_MARKER')
  const successor = await postSession(page, 'DELIBERATE_HEIR_MARKER')
  await bridgeReachableMap(page, cleared, { successor_trace_id: successor })
  await bridgeReachableMap(page, successor)

  await page.goto(`/live/${cleared}`)
  await settle(page)

  await expect(page).toHaveURL(new RegExp(`/live/${cleared}$`))
  await expect(page.getByText('DELIBERATE_SESSION_MARKER').first())
    .toBeVisible({ timeout: 20_000 })
})

test('an sdk run whose child is already named still rewrites onto the child', async ({ page }) => {
  // `successor_trace_id` is NOT clear-only: `register_alias` points
  // `_live_children` at the first child it sees, so a page on a run's own
  // `sdk-…` id carries it from the very first poll (sessions.py:1890). The
  // successor-outranks-rename guard must therefore not fire when the two name
  // the SAME session — on a reloaded or bookmarked `/live/<sdk-id>` the
  // canonical rewrite is the only thing that gets the operator onto the id
  // every other reader uses, and it gets exactly one chance.
  const child = await postSession(page, 'SDK_CHILD_MARKER')
  const run = await postSession(page, 'SDK_RUN_MARKER')
  await bridgeReachableMap(page, run,
    { canonical_trace_id: child, successor_trace_id: child })
  await bridgeReachableMap(page, child)

  await page.goto(`/live/${run}`)

  await expect(page).toHaveURL(new RegExp(`/live/${child}$`), { timeout: 20_000 })
})
