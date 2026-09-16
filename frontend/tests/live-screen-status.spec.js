/**
 * Terminal-derived live status on the /live card.
 *
 * The defect this guards: `phase` and the switcher dot were both derived from
 * span recency, and spans only arrive from hooks — so a session thinking,
 * streaming, or sitting inside one long tool call decayed to "idle" within
 * seconds of the last hook while the real terminal plainly showed it working.
 * The server now parses the pane (lib/agent_bridge/screen_state.py) and ships
 * the verdict as `summary.live_screen` plus a batch
 * `/api/bridge/screen-states` for the switcher.
 *
 * Both surfaces are stubbed at the network boundary rather than driven off a
 * real tmux pane: a live pane's busy/idle flips with wall-clock, which is
 * exactly the non-determinism these tests exist to remove. The server-side
 * parse itself is covered by tests/agent_bridge/test_screen_state.py against
 * verbatim capture-pane fixtures.
 *
 * Mirrors live-card.spec.js / live-picker.spec.js conventions: synthetic
 * `is_test: true` sessions via /api/session-spans, pinned 375x667.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { settle } from './helpers/overflow.js'

test.use({ viewport: { width: 375, height: 667 } })

const BUSY_SCREEN = {
  busy: true,
  verb: 'Sock-hopping',
  elapsed_sec: 334,
  tokens: 18200,
  tool: 'Bash(pytest -q -n auto)',
  ctx_pct: 11,
  draft: null,
}

async function postSession(page) {
  const traceId = randomUUID()
  const now = new Date().toISOString()
  const text = `SCREEN_FIXTURE_${traceId.slice(0, 8)}`
  const res = await page.request.post('/api/session-spans', {
    data: [
      { trace_id: traceId, span_id: `p-${traceId.slice(0, 8)}`, parent_id: null,
        name: 'prompt', start_time: now, attributes: { text, is_test: true } },
      { trace_id: traceId, span_id: `r-${traceId.slice(0, 8)}`, parent_id: null,
        name: 'assistant_response', start_time: now,
        attributes: { text: `${text} — response`, is_test: true } },
    ],
  })
  expect(res.ok()).toBeTruthy()
  return traceId
}

// Patch the summary the poll already fetches — the card must render the
// server's verdict, never re-derive one.
async function stubScreen(page, traceId, liveScreen) {
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const resp = await route.fetch()
    const json = await resp.json()
    await route.fulfill({
      response: resp,
      json: {
        ...json,
        phase: 'working',
        agent_phase: { main: 'working' },
        bridge_reachable: true,
        bridge_pane: '%3',
        live_screen: liveScreen,
      },
    })
  })
}

test.describe('NOW zone — what the terminal is doing', () => {
  test('a busy screen names the verb, the clock and the in-flight tool', async ({ page }) => {
    const traceId = await postSession(page)
    await stubScreen(page, traceId, BUSY_SCREEN)

    await page.goto(`/live/${traceId}`)
    await settle(page)

    const strip = page.locator('[data-testid="live-screen"]')
    await expect(strip).toBeVisible({ timeout: 10_000 })
    await expect(strip).toContainText('Sock-hopping')
    await expect(strip).toContainText('5m34s')  // fmtElapsedSeconds' own form
    await expect(strip).toContainText('18.2k tokens')
    await expect(strip).toContainText('Bash(pytest -q -n auto)')
  })

  test('the peek link opens the raw terminal sheet', async ({ page }) => {
    const traceId = await postSession(page)
    await stubScreen(page, traceId, BUSY_SCREEN)

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await page.locator('[data-testid="live-screen-peek"]').click()

    await expect(page.locator('[data-testid="live-sheet"]')).toBeVisible({ timeout: 5_000 })
    await expect(page.locator('[data-testid="live-sheet"]')).toContainText(/terminal/i)
  })

  test('an unreadable screen says so instead of claiming idle', async ({ page }) => {
    const traceId = await postSession(page)
    await stubScreen(page, traceId, null)

    await page.goto(`/live/${traceId}`)
    await settle(page)

    const none = page.locator('[data-testid="live-screen-none"]')
    await expect(none).toBeVisible({ timeout: 10_000 })
    await expect(none).toContainText('status unavailable')
    // No verb, no clock — the strip must not invent a state it cannot read.
    await expect(page.locator('.live-screen-verb')).toHaveCount(0)
  })

  test('the terminal chip is reachable from a NON-working state too', async ({ page }) => {
    // The question "what is this session really doing" has to be answerable
    // from whichever state the zone is in — an idle card that hides the chip
    // is the case the user has no other way out of.
    const traceId = await postSession(page)
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({
        response: resp,
        json: {
          ...json,
          phase: 'idle',
          agent_phase: { main: 'idle' },
          bridge_reachable: true,
          bridge_pane: '%3',
          live_screen: { busy: false, verb: null, elapsed_sec: null, tokens: null,
                         tool: null, ctx_pct: 9, draft: null },
        },
      })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)

    const chip = page.locator('[data-testid="live-screen-peek"]')
    await expect(chip).toBeVisible({ timeout: 10_000 })
    await chip.click()
    await expect(page.locator('[data-testid="live-sheet"]')).toContainText(/terminal/i)
  })

  test('an unbridged session shows no chip at all', async ({ page }) => {
    const traceId = await postSession(page)
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({
        response: resp,
        json: { ...json, bridge_reachable: false, live_screen: null },
      })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-screen"]')).toHaveCount(0)
  })

  test('an SDK-spawned run with no verdict renders nothing at all', async ({ page }) => {
    // `_steerability()` calls a regin-launched run reachable (the SDK channel
    // takes a steer) with a null pane. With no pane AND no status there is
    // nothing to say, and the chip would open a capture that always fails.
    const traceId = await postSession(page)
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({
        response: resp,
        json: { ...json, bridge_reachable: true, sdk_owned: true,
                bridge_pane: null, live_screen: null },
      })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="live-screen"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="live-screen-peek"]')).toHaveCount(0)
  })

  test('an SDK-spawned run reports its status with no terminal to offer', async ({ page }) => {
    // The runner answers in the screen's own shape for a run regin launched,
    // so the strip has a verdict to render while the pane that would back the
    // chip does not exist. Status without the chip is the whole point: the
    // operator cannot open a terminal that was never there.
    const traceId = await postSession(page)
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const resp = await route.fetch()
      const json = await resp.json()
      await route.fulfill({
        response: resp,
        json: {
          ...json,
          phase: 'working',
          agent_phase: { main: 'working' },
          bridge_reachable: true,
          sdk_owned: true,
          bridge_pane: null,
          live_screen: {
            busy: true, verb: 'Working', elapsed_sec: 41, tool: 'Bash',
            tokens: null, ctx_pct: null, draft: null,
          },
        },
      })
    })

    await page.goto(`/live/${traceId}`)
    await settle(page)

    const strip = page.locator('[data-testid="live-screen"]')
    await expect(strip).toBeVisible({ timeout: 10_000 })
    await expect(strip).toContainText('Working')
    await expect(strip).toContainText('41s')
    await expect(strip).toContainText('Bash')
    // Neither way into a terminal that was never there.
    await expect(page.locator('[data-testid="live-screen-peek"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="live-terminal-btn"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="live-screen-none"]')).toHaveCount(0)
  })
})

test.describe('Session switcher dots', () => {
  async function openPicker(page, traceId, listRows, states) {
    await page.route('**/api/sessions?kind=real*', (route) =>
      route.fulfill({ json: { sessions: listRows, items: listRows, cursor: null, has_more: false } }))
    await page.route('**/api/bridge/screen-states*', (route) =>
      route.fulfill({ json: { states } }))
    await page.goto(`/live/${traceId}`)
    await settle(page)
    await page.locator('[data-testid="live-switch"]').click()
    await expect(page.locator('[data-testid="live-sheet"]')).toBeVisible({ timeout: 5_000 })
  }

  function row(page, traceId) {
    return page.locator(`[data-testid="live-picker-row"][data-trace-id="${traceId}"] .live-status-dot`)
  }

  test('a busy screen outranks the recency rule for both dots', async ({ page }) => {
    const traceId = await postSession(page)
    const quiet = randomUUID()
    const stale = new Date(Date.now() - 45 * 60 * 1000).toISOString()
    // `busy` is old enough that recency alone would call it not-running, and
    // `quiet` is fresh enough that recency alone would call it running. The
    // screen reverses BOTH — which is the whole point.
    await openPicker(page, traceId,
      [{ trace_id: traceId, title: 'Busy', status: 'ended', last_seen: stale, started_at: stale, is_test: 0 },
       { trace_id: quiet, title: 'Quiet', status: 'active', last_seen: new Date().toISOString(), started_at: stale, is_test: 0 }],
      { [traceId]: { busy: true, verb: 'Thinking', elapsed_sec: 20 },
        [quiet]: { busy: false, verb: null, elapsed_sec: null } })

    await expect(row(page, traceId)).toHaveClass(/live-status-running/)
    await expect(row(page, quiet)).toHaveClass(/live-status-idle/)
  })

  test('no screen for a session falls back to the recency rule', async ({ page }) => {
    const traceId = await postSession(page)
    const stale = new Date(Date.now() - 45 * 60 * 1000).toISOString()
    await openPicker(page, traceId,
      [{ trace_id: traceId, title: 'Unbridged', status: 'ended', last_seen: stale, started_at: stale, is_test: 0 }],
      {})

    await expect(row(page, traceId)).toHaveClass(/live-status-ended/)
  })
})
