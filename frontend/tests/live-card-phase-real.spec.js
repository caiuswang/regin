/**
 * Real server → frontend phase path (no map stubbing).
 *
 * Every OTHER live-card phase test injects `phase` by patching the /map
 * response in-flight (bridgeReachableMap / phaseFields). That leaves the
 * genuine server→client path untested — a stale server emitting phase=None
 * once passed the whole suite. This spec hits the REAL
 * /api/sessions/<id>/map for a deterministic fixture, asserts the summary
 * carries phase + agent_phase.main in the documented vocabulary, and that the
 * rendered header dot/label is coherent with that server verdict.
 *
 * Determinism: an ENDED synthetic session is stable (phase pins to 'ended',
 * no timing window) — unlike a working/idle fixture whose phase drifts with
 * wall-clock age.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { settle } from './helpers/overflow.js'

test.use({ viewport: { width: 375, height: 667 } })

// web/blueprints/trace/sessions.py `_PHASE_PRECEDENCE`.
const PHASE_VOCAB = [
  'waiting-permission', 'waiting-input', 'working',
  // Reachable only for a run regin owns — the server reads them off the live
  // runner, which knows things no span-derived heuristic can see.
  'starting', 'stopping',
  'idle', 'inactive-stale', 'ended',
]

// LiveSessionView.vue `PHASE_STATUS` → the header status-dot class.
const PHASE_TO_DOT = {
  ended: 'live-status-ended',
  'inactive-stale': 'live-status-stale',
  idle: 'live-status-idle',
  'waiting-permission': 'live-status-waiting',
  'waiting-input': 'live-status-waiting',
  working: 'live-status-running',
  starting: 'live-status-running',
  stopping: 'live-status-waiting',
}

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

async function authHeaders(page) {
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  return { Authorization: `Bearer ${token}` }
}

test('real /map summary carries phase + agent_phase.main, and the header dot is coherent', async ({ page }) => {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  await post(page, [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, end_time: now, status_code: 'OK',
      attributes: { text: 'real-phase-path fixture', is_test: true } },
    { trace_id: traceId, span_id: `resp-${sfx}`, parent_id: null, name: 'assistant_response',
      start_time: now, end_time: now, status_code: 'OK',
      attributes: { text: 'final answer', is_test: true } },
    { trace_id: traceId, span_id: `end-${sfx}`, parent_id: null, name: 'session.end',
      start_time: now, end_time: now, status_code: 'OK',
      attributes: { reason: 'clear', is_test: true } },
  ])

  // Navigate FIRST (authHeaders reads localStorage, which throws on about:blank).
  await page.goto(`/live/${traceId}`)
  await settle(page)
  const header = page.locator('[data-testid="live-header"]')
  await expect(header).toBeVisible({ timeout: 10_000 })

  // The REAL server response — no route stub.
  const headers = await authHeaders(page)
  const resp = await page.request.get(
    `/api/sessions/${traceId}/map?shallow=1&limit=5`, { headers })
  expect(resp.ok()).toBeTruthy()
  const body = await resp.json()

  // The server actually populated the phase fields (the leak this guards is a
  // server emitting phase=None that the stubbed suite never caught).
  expect(body.phase, 'summary.phase missing/not in vocabulary').toBeTruthy()
  expect(PHASE_VOCAB).toContain(body.phase)
  expect(body.agent_phase, 'summary.agent_phase missing').toBeTruthy()
  expect(PHASE_VOCAB).toContain(body.agent_phase.main)
  // Deterministic ended fixture: both pin to 'ended'.
  expect(body.phase).toBe('ended')
  expect(body.agent_phase.main).toBe('ended')

  // The rendered header dot must match the server's main phase.
  const dot = header.locator('.live-status-dot')
  await expect(dot).toHaveClass(new RegExp(PHASE_TO_DOT[body.agent_phase.main]))
  await expect(header.locator('.live-hd-status')).toContainText('finished')
})

// ── The runner's own verdict, for a session regin launched ───────────
//
// These two phases have no span-derived source at all, so unlike the fixture
// above they are stubbed: the point is that the client renders what the server
// can now say, not that the server derives it (that is pinned in
// tests/agent_sdk/test_run_phase.py).

async function stubbedPhase(page, traceId, phase) {
  await page.route('**/api/sessions/*/map*', async (route) => {
    const res = await route.fetch()
    let json = {}
    try { json = await res.json() } catch { json = {} }
    await route.fulfill({
      json: { ...json, sdk_owned: true, bridge_reachable: true,
              phase, agent_phase: { main: phase } },
    })
  })
  await page.goto(`/live/${traceId}`)
  await settle(page)
}

async function endedFixture(page) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  await post(page, [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null,
      name: 'prompt', start_time: now, end_time: now, status_code: 'OK',
      attributes: { text: 'runner-phase fixture', is_test: true } },
  ])
  return traceId
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('a starting run reads "starting", not the heuristic\'s guess', async ({ page }) => {
  const traceId = await endedFixture(page)
  await stubbedPhase(page, traceId, 'starting')

  const header = page.locator('[data-testid="live-header"]')
  await expect(header.locator('.live-hd-status')).toContainText('starting')
  await expect(header.locator('.live-status-dot'))
    .toHaveClass(/live-status-running/)
  await expect(page.getByTestId('live-now')).toHaveAttribute('data-state', 'starting')
  // Nothing can be typed into a session that is still coming up.
  await expect(page.getByTestId('live-composer')).toHaveCount(0)
  await expect(page.getByTestId('live-cancel-btn')).toHaveCount(0)
})

test('a stopping run says so, and offers neither stop nor cancel', async ({ page }) => {
  const traceId = await endedFixture(page)
  await stubbedPhase(page, traceId, 'stopping')

  const header = page.locator('[data-testid="live-header"]')
  await expect(header.locator('.live-hd-status')).toContainText('stopping')
  await expect(page.getByTestId('live-now')).toHaveAttribute('data-state', 'stopping')
  // Already asked; a second Stop is noise, and there is no turn to cancel.
  await expect(page.getByTestId('live-stop')).toHaveCount(0)
  await expect(page.getByTestId('live-cancel-btn')).toHaveCount(0)
})

test('an owned run working through a silent tool call reads running, not inactive',
  async ({ page }) => {
    // The bug: no spans for longer than the stale window, so the heuristic said
    // "inactive" while the child was plainly working. The runner's verdict wins.
    const traceId = await endedFixture(page)
    await stubbedPhase(page, traceId, 'working')

    const header = page.locator('[data-testid="live-header"]')
    await expect(header.locator('.live-hd-status')).toContainText('running')
    await expect(header.locator('.live-status-dot'))
      .toHaveClass(/live-status-running/)
    await expect(page.getByTestId('live-cancel-btn')).toBeVisible()
  })
