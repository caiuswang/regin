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

// ── Cancel the turn, keep the session ────────────────────────────────
//
// The interrupt route existed server-side with no caller at all, so an
// operator whose agent was off down the wrong path could only kill the whole
// session. Cancel is deliberately NOT gated on `sdk_owned` the way Stop is:
// the route serves both tiers, sending a typed interrupt to a run regin owns
// and the Escape a human would press to a pane it merely watches.

test('a working session offers cancel on both tiers', async ({ page }) => {
  const traceId = await postSession(page)
  // Terminal tier: no typed channel, so no Stop — but Escape still reaches it.
  await patchMap(page, {
    sdk_owned: false, bridge_reachable: true, phase: 'working',
    agent_phase: { main: 'working' },
  })

  await openCard(page, traceId)

  await expect(page.getByTestId('live-cancel-btn')).toBeVisible()
  await expect(page.getByTestId('live-stop')).toHaveCount(0)
})

test('cancel posts once, on one tap, and reports what the route said',
  async ({ page }) => {
    const traceId = await postSession(page)
    await patchMap(page, {
      sdk_owned: true, bridge_reachable: true, phase: 'working',
      agent_phase: { main: 'working' },
    })
    let cancelled = 0
    await page.route('**/api/sessions/*/bridge-interrupt', (route) => {
      cancelled += 1
      return route.fulfill({ json: { delivered: true, detail: 'interrupt sent' } })
    })

    await openCard(page, traceId)
    // One tap, no arm/confirm: Escape is one keypress in a terminal, and the
    // session survives either way.
    await page.getByTestId('live-cancel-btn').click()
    await settle(page)

    expect(cancelled).toBe(1)
    await expect(page.getByTestId('live-cancel-detail'))
      .toContainText('interrupt sent')
  })

test('a refused cancel says why and is not a dead end', async ({ page }) => {
  const traceId = await postSession(page)
  await patchMap(page, {
    sdk_owned: false, bridge_reachable: true, phase: 'working',
    agent_phase: { main: 'working' },
  })
  await page.route('**/api/sessions/*/bridge-interrupt', (route) => route.fulfill({
    json: { delivered: false, detail: 'no reachable session' },
  }))

  await openCard(page, traceId)
  await page.getByTestId('live-cancel-btn').click()
  await settle(page)

  await expect(page.getByTestId('live-cancel-detail'))
    .toContainText('no reachable session')
  await page.getByTestId('live-cancel-retry').click()
  await expect(page.getByTestId('live-cancel-btn')).toBeVisible()
})

test('there is nothing to cancel while idle or finished', async ({ page }) => {
  const traceId = await postSession(page)
  const serve = await patchMap(page, {
    sdk_owned: true, bridge_reachable: true, phase: 'idle',
    agent_phase: { main: 'idle' },
  })

  await openCard(page, traceId)
  await expect(page.getByTestId('live-cancel-btn')).toHaveCount(0)
  // Stop still applies — an idle run is exactly what you reclaim.
  await expect(page.getByTestId('live-stop-arm')).toBeVisible()

  serve({ phase: 'ended', agent_phase: { main: 'ended' } })
  await expect(page.getByTestId('live-cancel-btn')).toHaveCount(0)
  await expect(page.getByTestId('live-stop')).toHaveCount(0)
})

// ── Editing and dropping a queued prompt ─────────────────────────────
//
// Only the SDK tier's queue is regin's to write, and the server signals that
// per ROW by giving it an id. A transcript-derived row has none and must stay
// read-only rather than offering a control that changes nothing.

test('controls follow the row: sdk edits, bridge only dismisses, id-less is inert',
  async ({ page }) => {
    const traceId = await postSession(page)
    await patchMap(page, {
      sdk_owned: true,
      queued_prompts: [
        { id: 'q1', content: 'mine to edit', source: 'sdk' },
        // A bridge steer's keystrokes are already in the pane: its chip can
        // be dismissed but never rewritten.
        { id: 'b7', content: 'typed into the pane', source: 'bridge' },
        { content: "claude code's, not mine" },
      ],
    })

    await openCard(page, traceId)

    await expect(page.getByTestId('live-queued-item')).toHaveCount(3)
    await expect(page.getByTestId('live-queued-edit')).toHaveCount(1)
    await expect(page.getByTestId('live-queued-remove')).toHaveCount(2)
  })

test('a queued prompt can be rewritten in place', async ({ page }) => {
  const traceId = await postSession(page)
  const serve = await patchMap(page, {
    sdk_owned: true,
    queued_prompts: [
      { id: 'q1', content: 'first', source: 'sdk' },
      { id: 'q2', content: 'teh second', source: 'sdk' },
    ],
  })
  let sent = null
  await page.route('**/api/agent-runs/*/queue/q2', async (route) => {
    sent = route.request().postDataJSON()
    return route.fulfill({ json: { updated: true, detail: 'prompt updated' } })
  })

  await openCard(page, traceId)
  await page.getByTestId('live-queued-edit').nth(1).click()
  await page.getByTestId('live-queued-input').fill('the second')
  await page.getByTestId('live-queued-save').click()
  await settle(page)

  expect(sent).toEqual({ prompt: 'the second' })
  // Optimistic: the poll is seconds away, so the new text shows at once — and
  // in its own slot, not moved behind the prompt after it.
  const items = page.getByTestId('live-queued-item')
  await expect(items.nth(0)).toContainText('first')
  await expect(items.nth(1)).toContainText('the second')

  // And it survives the server catching up.
  serve({
    queued_prompts: [
      { id: 'q1', content: 'first', source: 'sdk' },
      { id: 'q2', content: 'the second', source: 'sdk' },
    ],
  })
  await settle(page)
  await expect(items.nth(1)).toContainText('the second')
})

test('a removed prompt goes at once and stays gone across the poll',
  async ({ page }) => {
    const traceId = await postSession(page)
    const serve = await patchMap(page, {
      sdk_owned: true,
      queued_prompts: [
        { id: 'q1', content: 'keep me', source: 'sdk' },
        { id: 'q2', content: 'drop me', source: 'sdk' },
      ],
    })
    let method = null
    await page.route('**/api/agent-runs/*/queue/q2', (route) => {
      method = route.request().method()
      return route.fulfill({ json: { removed: true, detail: 'prompt removed' } })
    })

    await openCard(page, traceId)
    await page.getByTestId('live-queued-remove').nth(1).click()

    await expect(page.getByTestId('live-queued-item')).toHaveCount(1)
    expect(method).toBe('DELETE')

    // The next poll still carries it (it was in flight when the tap landed) —
    // the row must not flicker back.
    await settle(page)
    await expect(page.getByTestId('live-queued-item')).toHaveCount(1)

    serve({ queued_prompts: [{ id: 'q1', content: 'keep me', source: 'sdk' }] })
    await settle(page)
    await expect(page.getByTestId('live-queued-item')).toHaveCount(1)
    await expect(page.getByTestId('live-queued-item')).toContainText('keep me')
  })

test('a refused removal puts the row back and says why', async ({ page }) => {
  const traceId = await postSession(page)
  await patchMap(page, {
    sdk_owned: true,
    queued_prompts: [{ id: 'q1', content: 'already running', source: 'sdk' }],
  })
  await page.route('**/api/agent-runs/*/queue/q1', (route) => route.fulfill({
    json: { removed: false, detail: 'that prompt is no longer queued' },
  }))

  await openCard(page, traceId)
  await page.getByTestId('live-queued-remove').click()
  await settle(page)

  // The interesting failure: the poll that rendered the row is a turn out of
  // date, and the operator has to read that rather than watch it reappear.
  await expect(page.getByTestId('live-queued-notice'))
    .toContainText('no longer queued')
  await expect(page.getByTestId('live-queued-item')).toHaveCount(1)
})

test('discarding an edit leaves the queued prompt untouched', async ({ page }) => {
  const traceId = await postSession(page)
  await patchMap(page, {
    sdk_owned: true,
    queued_prompts: [{ id: 'q1', content: 'as typed', source: 'sdk' }],
  })
  let patched = 0
  await page.route('**/api/agent-runs/*/queue/q1', (route) => {
    patched += 1
    return route.fulfill({ json: { updated: true, detail: 'prompt updated' } })
  })

  await openCard(page, traceId)
  await page.getByTestId('live-queued-edit').click()
  await page.getByTestId('live-queued-input').fill('never mind')
  await page.getByTestId('live-queued-cancel-edit').click()
  await settle(page)

  expect(patched).toBe(0)
  await expect(page.getByTestId('live-queued-item')).toContainText('as typed')
})

test('a sent steer renders only what the server serves — no client echo, no ghost',
  async ({ page }) => {
    // The old optimistic echo expired on a TTL and could resurface as an
    // un-removable `⧗ steering…` chip for text the agent will never run (an
    // executed /exit, a removed prompt). The chip feed is now a pure render
    // of the server's queue: nothing shows before a poll represents the
    // send, and a dropped row leaves nothing behind to come back.
    const traceId = await postSession(page)
    const serve = await patchMap(page, {
      sdk_owned: true, bridge_reachable: true, phase: 'working',
      agent_phase: { main: 'working' }, queued_prompts: [],
    })
    await page.route('**/api/sessions/*/bridge-send', (route) => route.fulfill({
      json: { delivered: true, detail: 'prompt queued', id: 1 },
    }))
    await page.route('**/api/agent-runs/*/queue/q1', (route) => route.fulfill({
      json: { removed: true, detail: 'prompt removed' },
    }))

    await openCard(page, traceId)
    await page.getByTestId('live-composer-ta').fill('steer me')
    await page.getByTestId('live-composer-send').click()
    await settle(page)
    // Delivered, but not yet served by a poll: no chip, only the composer's
    // "delivered" flash. The next poll is what makes it visible.
    await expect(page.getByTestId('live-queued-item')).toHaveCount(0)

    serve({ queued_prompts: [{ id: 'q1', content: 'steer me', source: 'sdk' }] })
    await expect(page.getByTestId('live-queued-item')).toHaveCount(1)
    await expect(page.getByTestId('live-queued-remove')).toHaveCount(1)

    await page.getByTestId('live-queued-remove').click()
    await expect(page.getByTestId('live-queued-item')).toHaveCount(0)

    // A marker row proves a poll landed with the removed prompt gone
    // server-side — the exact moment the old echo used to resurface.
    serve({ queued_prompts: [{ id: 'q9', content: 'MARKER', source: 'sdk' }] })
    const rows = page.getByTestId('live-queued-item')
    await expect(rows.filter({ hasText: 'MARKER' })).toHaveCount(1,
      { timeout: 15_000 })
    await expect(rows).toHaveCount(1)
  })

test('editing a steer you just sent shows one row, not the old text beside it',
  async ({ page }) => {
    const traceId = await postSession(page)
    const serve = await patchMap(page, {
      sdk_owned: true, bridge_reachable: true, phase: 'working',
      agent_phase: { main: 'working' }, queued_prompts: [],
    })
    await page.route('**/api/sessions/*/bridge-send', (route) => route.fulfill({
      json: { delivered: true, detail: 'prompt queued', id: 1 },
    }))
    await page.route('**/api/agent-runs/*/queue/q1', (route) => route.fulfill({
      json: { updated: true, detail: 'prompt updated' },
    }))

    await openCard(page, traceId)
    await page.getByTestId('live-composer-ta').fill('teh typo')
    await page.getByTestId('live-composer-send').click()
    serve({ queued_prompts: [{ id: 'q1', content: 'teh typo', source: 'sdk' }] })
    await expect(page.getByTestId('live-queued-edit')).toHaveCount(1)

    await page.getByTestId('live-queued-edit').click()
    await page.getByTestId('live-queued-input').fill('the typo')
    await page.getByTestId('live-queued-save').click()
    await settle(page)

    // Marker row again: it proves a poll landed carrying the EDITED text, so
    // the original is no longer represented and a stale echo would show up
    // beside it. Without one this asserts before the poll and passes for free.
    serve({
      queued_prompts: [
        { id: 'q1', content: 'the typo', source: 'sdk' },
        { id: 'q9', content: 'MARKER', source: 'sdk' },
      ],
    })
    const items = page.getByTestId('live-queued-item')
    await expect(items.filter({ hasText: 'MARKER' })).toHaveCount(1,
      { timeout: 15_000 })

    await expect(items).toHaveCount(2)
    await expect(items.nth(0)).toContainText('the typo')
  })

test('an over-long edit sends and shows the same text the server will keep',
  async ({ page }) => {
    // The route stores prompt[:8000]. An optimistic override holding the
    // untruncated text would never match what comes back, so it would mask the
    // row with text the runner does not hold for the rest of the session.
    const traceId = await postSession(page)
    const serve = await patchMap(page, {
      sdk_owned: true,
      queued_prompts: [{ id: 'q1', content: 'short', source: 'sdk' }],
    })
    let sentLen = null
    await page.route('**/api/agent-runs/*/queue/q1', async (route) => {
      sentLen = route.request().postDataJSON().prompt.length
      return route.fulfill({ json: { updated: true, detail: 'prompt updated' } })
    })

    await openCard(page, traceId)
    await page.getByTestId('live-queued-edit').click()
    await page.getByTestId('live-queued-input').fill('x'.repeat(9000))
    await page.getByTestId('live-queued-save').click()
    await settle(page)

    expect(sentLen).toBe(8000)

    // The server echoes back what it stored; the override settles rather than
    // masking the row forever.
    serve({
      queued_prompts: [{ id: 'q1', content: 'x'.repeat(8000), source: 'sdk' }],
    })
    await settle(page)
    const shown = await page.getByTestId('live-queued-item').innerText()
    expect(shown.replace(/[^x]/g, '').length).toBe(8000)
  })
