/**
 * The follow-up composer's run controls: Cancel the turn in flight, and Stop
 * the agent outright.
 *
 * Both routes are tier-agnostic server-side — a session regin launched over
 * the Agent SDK and a terminal it only reaches through tmux hit the same two
 * endpoints — so what has to be pinned here is that the bar sends exactly
 * those two, that Stop cannot fire without arming, and that a refusal stays
 * readable and recoverable rather than reading as a silent success.
 *
 * Reachability is patched onto the map response; a live bridge pane is not
 * something an E2E run can stand up.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`

// The REAL body `/api/sessions/<id>/shutdown` returns — `live_session.
// graceful_close`, pinned by tests/agent_bridge/test_live_shutdown.py. It has
// no `delivered` key, unlike every other bridge route, and a mock that invents
// one certifies a bar that renders successful stops as red failures.
const SHUTDOWN_OK = {
  tier: 'sdk', live: true, closed: true, interrupted: false, detail: 'stopping',
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function seed(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const spans = [
    {
      trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'Add token-bucket rate limiting.', is_test: true },
    },
    {
      trace_id: traceId, span_id: `a-${p8}`, parent_id: prompt, name: 'assistant_response',
      start_time: at(1), duration_ms: 2200,
      attributes: { text: 'Here is where the limiter should hook in.', is_test: true },
    },
  ]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.ended_at = null
    body.bridge_reachable = true
    body.model = 'sonnet-4.6'
    await route.fulfill({ response: res, json: body })
  })

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine:not(.event-spine-row)')).toBeVisible({ timeout: 20_000 })
  return traceId
}

// The controls live in the open panel, not on the resting bubble.
async function openComposer(page) {
  await page.getByTestId('followup-pill').click()
  await expect(page.getByTestId('followup-textarea')).toBeFocused()
}

test('Cancel posts bridge-interrupt in one click and recovers to the buttons', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = await seed(page)
  let hits = 0
  await page.route(`**/api/sessions/${traceId}/bridge-interrupt`, async (route) => {
    hits += 1
    await route.fulfill({ json: { delivered: true, detail: 'interrupt sent' } })
  })

  await openComposer(page)
  await page.getByTestId('composer-cancel').click()

  await expect(page.getByTestId('composer-run-detail')).toContainText('interrupt sent')
  expect(hits).toBe(1)
  // Nothing about a cancel is terminal, so a second one must stay reachable.
  await expect(page.getByTestId('composer-cancel')).toBeVisible({ timeout: 10_000 })
})

test('Stop arms before it fires, and posts shutdown — not the SDK-only run route', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = await seed(page)
  let shutdowns = 0
  await page.route(`**/api/sessions/${traceId}/shutdown`, async (route) => {
    shutdowns += 1
    await route.fulfill({ json: SHUTDOWN_OK })
  })
  // The tmux tier has no agent-run row; a bar that reached for it would stop
  // nothing there, so assert the composer never calls it.
  let runStops = 0
  await page.route('**/api/agent-runs/*/stop', async (route) => {
    runStops += 1
    await route.fulfill({ json: { delivered: false, detail: 'no live agent session' } })
  })

  await openComposer(page)
  await page.getByTestId('composer-stop-arm').click()
  expect(shutdowns, 'arming must not stop anything').toBe(0)
  await expect(page.getByTestId('composer-run-controls')).toContainText('Stop the agent?')

  await page.getByTestId('composer-stop-confirm').click()
  // The class assertion is not redundant: the failure branch renders
  // `✗ stopping`, which satisfies the text match on its own.
  const detail = page.getByTestId('composer-run-detail')
  await expect(detail).toContainText('stopping')
  await expect(detail).not.toHaveClass(/is-failed/)
  expect(shutdowns).toBe(1)
  expect(runStops).toBe(0)
})

test('An armed Stop can be backed out of', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = await seed(page)
  let shutdowns = 0
  await page.route(`**/api/sessions/${traceId}/shutdown`, async (route) => {
    shutdowns += 1
    await route.fulfill({ json: SHUTDOWN_OK })
  })

  await openComposer(page)
  await page.getByTestId('composer-stop-arm').click()
  await page.getByTestId('composer-stop-keep').click()

  await expect(page.getByTestId('composer-stop-arm')).toBeVisible()
  expect(shutdowns).toBe(0)
})

test('A refused cancel shows the reason and offers a way back', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = await seed(page)
  await page.route(`**/api/sessions/${traceId}/bridge-interrupt`, route =>
    route.fulfill({ json: { delivered: false, detail: 'no reachable session' } }))

  await openComposer(page)
  await page.getByTestId('composer-cancel').click()

  const detail = page.getByTestId('composer-run-detail')
  await expect(detail).toContainText('no reachable session')
  await expect(detail).toHaveClass(/is-failed/)
  await page.getByTestId('composer-run-retry').click()
  await expect(page.getByTestId('composer-cancel')).toBeVisible()
})

// `/shutdown` reports success through `closed`/`tier`, never `delivered`. A bar
// that reads the wrong key inverts the outcome, so pin both readings of "it
// took" and the one genuine failure the route can report.
test.describe('the shutdown contract, not the delivered contract', () => {
  test('a closed session reads as success, not a red failure', async ({ page }) => {
    test.setTimeout(90_000)
    const traceId = await seed(page)
    await page.route(`**/api/sessions/${traceId}/shutdown`, route =>
      route.fulfill({ json: SHUTDOWN_OK }))

    await openComposer(page)
    await page.getByTestId('composer-stop-arm').click()
    await page.getByTestId('composer-stop-confirm').click()

    const detail = page.getByTestId('composer-run-detail')
    await expect(detail).toContainText('✓ stopping')
    await expect(detail).not.toHaveClass(/is-failed/)
    await expect(page.getByTestId('composer-run-retry')).toHaveCount(0)
  })

  test('a null tier is "nothing left to close", not a refusal', async ({ page }) => {
    test.setTimeout(90_000)
    const traceId = await seed(page)
    await page.route(`**/api/sessions/${traceId}/shutdown`, route =>
      route.fulfill({ json: {
        tier: null, live: false, closed: false, interrupted: false,
        detail: 'no manageable tier holds this session',
      } }))

    await openComposer(page)
    await page.getByTestId('composer-stop-arm').click()
    await page.getByTestId('composer-stop-confirm').click()

    // A tick on the server's own sentence would read as a tick on nothing
    // having happened, so the bar words this outcome itself.
    const detail = page.getByTestId('composer-run-detail')
    await expect(detail).not.toHaveClass(/is-failed/)
    await expect(detail).toContainText('already stopped')
    await expect(detail).not.toContainText('no manageable tier')
  })

  test('a null tier regin could not establish is a failure, not a tick', async ({ page }) => {
    test.setTimeout(90_000)
    const traceId = await seed(page)
    // The registry named a pane and the probe could not read it. Same null
    // tier as a clean close, opposite meaning: the agent may still be running
    // and was never touched, so a tick here reports a stop that never happened.
    await page.route(`**/api/sessions/${traceId}/shutdown`, route =>
      route.fulfill({ json: {
        tier: null, live: false, closed: false, interrupted: false,
        undetermined: true,
        detail: 'a pane is registered but regin could not read it; the session may still be running',
      } }))

    await openComposer(page)
    await page.getByTestId('composer-stop-arm').click()
    await page.getByTestId('composer-stop-confirm').click()

    // Text first: it is the assertion that waits for the response to land.
    // Leading with the class check races the POST under parallel load and
    // reddens on a component that is merely still `stopping`.
    const detail = page.getByTestId('composer-run-detail')
    await expect(detail).toContainText('may still be running')
    await expect(detail).toHaveClass(/is-failed/)
    await expect(detail).not.toContainText('already stopped')
  })

  test('a tmux quit that did not land is a failure, worded by the server', async ({ page }) => {
    test.setTimeout(90_000)
    const traceId = await seed(page)
    // The turn WAS cancelled but the pane is still running claude — the
    // distinction the operator is owed, and the one a boolean would flatten.
    await page.route(`**/api/sessions/${traceId}/shutdown`, route =>
      route.fulfill({ json: {
        tier: 'tmux', live: true, closed: false, interrupted: true,
        detail: 'turn cancelled, but /exit did not take',
      } }))

    await openComposer(page)
    await page.getByTestId('composer-stop-arm').click()
    await page.getByTestId('composer-stop-confirm').click()

    const detail = page.getByTestId('composer-run-detail')
    await expect(detail).toContainText('/exit did not take')
    await expect(detail).toHaveClass(/is-failed/)
    await page.getByTestId('composer-run-retry').click()
    await expect(page.getByTestId('composer-stop-arm')).toBeVisible()
  })
})

test('the armed row — the widest state — still fits a phone', async ({ page }) => {
  test.setTimeout(90_000)
  await page.setViewportSize({ width: 390, height: 844 })
  await seed(page)
  await openComposer(page)
  await page.getByTestId('composer-stop-arm').click()
  await expect(page.getByTestId('composer-run-controls')).toContainText('Stop the agent?')

  const row = page.locator('.followup-panel [data-testid="composer-run-controls"]').locator('..')
  const fit = await row.evaluate(el => el.scrollWidth <= el.clientWidth)
  expect(fit, 'the composer action row must not scroll sideways while armed').toBeTruthy()
})

test('The controls ride the composer, so they are absent until it opens', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)
  await expect(page.getByTestId('followup-pill')).toBeVisible()
  await expect(page.getByTestId('composer-run-controls')).toHaveCount(0)
  await openComposer(page)
  await expect(page.getByTestId('composer-run-controls')).toBeVisible()
})
