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
