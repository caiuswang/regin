/**
 * Stopping a regin-owned run, and what its queue looks like while it waits
 * (`live/LiveStopControl.vue`, `live/LiveQueuedChips.vue`).
 *
 * Two defects this pins shut:
 *  - a queued steer was represented ONLY by the client's optimistic echo, so it
 *    showed for the echo's TTL and then vanished while still queued. The server
 *    now serves the SDK run's real queue (`source:'sdk'`), so the chip's life is
 *    the prompt's life.
 *  - the stop route existed with no client at all: a launched run could not be
 *    ended from the browser, only by restarting the server.
 *
 * Conventions mirror live-launch-run.spec.js: a synthetic `is_test: true`
 * session so the card has a tail, the map response patched in flight (rather
 * than replaced, so the rest of the payload stays real), and `settle()` between
 * interactions.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { settle, contentOverflow } from './helpers/overflow.js'

test.use({ viewport: { width: 390, height: 780 } })

// The card polls on a timer, so a poll is routinely in flight when a test
// ends — tear the handlers down rather than letting that one error the run.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function postSession(page) {
  const traceId = randomUUID()
  const now = new Date().toISOString()
  const sfx = traceId.slice(0, 8)
  const res = await page.request.post('/api/session-spans', {
    data: [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: `STOP_FIXTURE_${sfx}`, is_test: true } },
      { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null,
        name: 'assistant_response', start_time: now,
        attributes: { text: 'working on it', is_test: true } },
    ],
  })
  expect(res.ok()).toBeTruthy()
  return traceId
}

/**
 * Patch the live poll's payload without discarding the real one. Returns a
 * setter so a test can change what the SERVER serves between polls — which is
 * the thing under test here (the chip's life is the queue entry's life), and
 * re-routing mid-test would race the in-flight poll instead.
 */
async function patchMap(page, patch) {
  const state = { patch }
  await page.route('**/api/sessions/*/map*', async (route) => {
    const res = await route.fetch()
    let json = {}
    try { json = await res.json() } catch { json = {} }
    await route.fulfill({ json: { ...json, ...state.patch } })
  })
  return (next) => { state.patch = { ...state.patch, ...next } }
}

async function openCard(page, traceId) {
  await page.goto(`/live/${traceId}`)
  await settle(page)
}

test('a session regin only observes offers no stop', async ({ page }) => {
  const traceId = await postSession(page)
  await patchMap(page, { sdk_owned: false, bridge_reachable: false })

  await openCard(page, traceId)

  // A button that stops nothing is worse than no button.
  await expect(page.getByTestId('live-stop')).toHaveCount(0)
})

test('a regin-owned run can be stopped, behind one confirm', async ({ page }) => {
  const traceId = await postSession(page)
  await patchMap(page, { sdk_owned: true, bridge_reachable: true })
  let stopped = 0
  await page.route('**/api/agent-runs/*/stop', (route) => {
    stopped += 1
    return route.fulfill({ json: { delivered: true, detail: 'stopping' } })
  })

  await openCard(page, traceId)
  await expect(page.getByTestId('live-stop-arm')).toBeVisible()

  // Arming must not stop anything on its own — the tap that ends a run is
  // the second one.
  await page.getByTestId('live-stop-arm').click()
  await settle(page)
  expect(stopped).toBe(0)
  await expect(page.getByTestId('live-stop-confirm')).toBeVisible()

  await page.getByTestId('live-stop-confirm').click()
  await settle(page)
  expect(stopped).toBe(1)
  await expect(page.getByTestId('live-stop-detail')).toContainText('stopping')
})

test('cancelling an armed stop leaves the run alone', async ({ page }) => {
  const traceId = await postSession(page)
  await patchMap(page, { sdk_owned: true, bridge_reachable: true })
  let stopped = 0
  await page.route('**/api/agent-runs/*/stop', (route) => {
    stopped += 1
    return route.fulfill({ json: { delivered: true, detail: 'stopping' } })
  })

  await openCard(page, traceId)
  await page.getByTestId('live-stop-arm').click()
  await page.getByTestId('live-stop-cancel').click()
  await settle(page)

  expect(stopped).toBe(0)
  await expect(page.getByTestId('live-stop-arm')).toBeVisible()
})

test('a refused stop says why instead of claiming success', async ({ page }) => {
  const traceId = await postSession(page)
  await patchMap(page, { sdk_owned: true, bridge_reachable: true })
  await page.route('**/api/agent-runs/*/stop', (route) => route.fulfill({
    json: { delivered: false, detail: 'no live agent session' },
  }))

  await openCard(page, traceId)
  await page.getByTestId('live-stop-arm').click()
  await page.getByTestId('live-stop-confirm').click()
  await settle(page)

  await expect(page.getByTestId('live-stop-detail'))
    .toContainText('no live agent session')

  // A refusal must not be a dead end — a dropped request or a momentarily
  // unreachable run would otherwise cost a reload to get the button back.
  await page.getByTestId('live-stop-retry').click()
  await expect(page.getByTestId('live-stop-arm')).toBeVisible()
})

test('the served queue is what renders — 0, 1, then 3 chips in FIFO order',
  async ({ page }) => {
    const traceId = await postSession(page)
    const serve = await patchMap(page, { sdk_owned: true, queued_prompts: [] })

    await openCard(page, traceId)
    await expect(page.getByTestId('live-queued-item')).toHaveCount(0)

    serve({ queued_prompts: [{ content: 'only one', source: 'sdk' }] })
    await expect(page.getByTestId('live-queued-item')).toHaveCount(1)

    serve({
      queued_prompts: [
        { content: 'oldest', source: 'sdk' },
        { content: 'middle', source: 'sdk' },
        { content: 'newest', source: 'sdk' },
      ],
    })

    const items = page.getByTestId('live-queued-item')
    await expect(items).toHaveCount(3)
    // Oldest first — the order the agent will actually run them in.
    await expect(items.nth(0)).toContainText('oldest')
    await expect(items.nth(2)).toContainText('newest')
  })

test('a queued sdk prompt drops off when the server stops serving it',
  async ({ page }) => {
    const traceId = await postSession(page)
    const serve = await patchMap(page, {
      sdk_owned: true,
      queued_prompts: [{ content: 'steer me', source: 'sdk' }],
    })

    await openCard(page, traceId)
    await expect(page.getByTestId('live-queued-item')).toHaveCount(1)

    // Its turn started: the queue no longer holds it. The chip goes with it on
    // the next poll — no reload, and no client-side TTL involved either way.
    serve({ queued_prompts: [] })
    await expect(page.getByTestId('live-queued-item')).toHaveCount(0)
  })

test('the stop control and a long queued prompt fit a 390px card',
  async ({ page }) => {
    const traceId = await postSession(page)
    await patchMap(page, {
      sdk_owned: true,
      bridge_reachable: true,
      queued_prompts: [{
        content: 'refactor the serve-time merge so retired placeholders never '
          + 'reach the append-only conversation cards, then re-run the suite',
        source: 'sdk',
      }],
    })

    await openCard(page, traceId)
    await page.getByTestId('live-stop-arm').click()
    await settle(page)

    // Armed, the control is three items wide — the widest it ever gets.
    await expect(page.getByTestId('live-stop-confirm')).toBeVisible()

    const m = await contentOverflow(page)
    test.skip(!m.pane, 'content pane not present (redirected to login/other)')
    expect(
      m.scrollWidth,
      `/live card overflows (${m.scrollWidth} > ${m.clientWidth}); offenders: ${m.offenders.join(', ') || 'unknown'}`
    ).toBeLessThanOrEqual(m.clientWidth + 1)
  })
