/**
 * SessionTraceView: the live poll is self-terminating for a CLOSED session.
 *
 * The trace view polls `/map?shallow` every ~4s to converge its tail to the DB,
 * and each poll also fires a backend transcript rescan. That is pure waste once
 * a session has ended and its tail has stopped growing. The view must therefore:
 *   - run a BOUNDED catch-up (crash recovery) when opened on an already-ended
 *     session — a few reconciles at most, not a perpetual poll;
 *   - then STOP, firing no further `/map?shallow` (live-tail) requests.
 *
 * A `session.end` span sets `sessions.ended_at` (lib/trace/.../ingest.py
 * `_handle_session_end`), so the `/map` summary reports the session as closed.
 *
 * Data is injected via `/api/session-spans` with `is_test=true` so the trace is
 * invisible in the sessions list and the test is portable to any clean DB.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

// Live-tail / initial-load map fetches: `/sessions/<id>/map?shallow=1&limit=...`
// with NO `before_id` (load-older carries before_id and is scroll-driven only).
function isLiveTailMap(url, traceId) {
  return url.includes(`/sessions/${traceId}/map`) && !url.includes('before_id=')
}

test('a closed session runs a bounded catch-up then stops polling', async ({ page }) => {
  const traceId = randomUUID()
  const promptId = `prompt-${traceId.slice(0, 8)}`
  const endId = `end-${traceId.slice(0, 8)}`

  // Seed: one settled prompt + a session.end span → the session is CLOSED
  // (ended_at set) before the view ever opens.
  await post(page, [
    {
      trace_id: traceId, span_id: promptId, parent_id: null, name: 'prompt',
      start_time: '2026-05-17T08:00:00.000000', end_time: '2026-05-17T08:00:01.000000',
      status_code: 'OK', attributes: { text: 'closed session fixture prompt', is_test: true },
    },
    {
      trace_id: traceId, span_id: endId, parent_id: null, name: 'session.end',
      start_time: '2026-05-17T08:00:05.000000', end_time: '2026-05-17T08:00:05.000000',
      status_code: 'OK', attributes: { reason: 'clear', is_test: true },
    },
  ])

  // Count every live-tail map fetch (initial load + bounded catch-up + any poll).
  let liveTailMaps = 0
  page.on('request', (req) => {
    if (req.method() === 'GET' && isLiveTailMap(req.url(), traceId)) liveTailMaps += 1
  })

  await page.goto(`/trace/sessions/${traceId}`)
  await expect(page.getByText('closed session fixture prompt').first())
    .toBeVisible({ timeout: 10_000 })

  // Let the bounded catch-up settle (it reconciles only while the tail keeps
  // advancing, capped at CLOSED_SYNC_MAX_TICKS=3), then snapshot the count.
  await page.waitForTimeout(3_000)
  const afterCatchup = liveTailMaps

  // The catch-up is bounded: one initial load + at most 3 reconciles.
  expect(afterCatchup).toBeGreaterThanOrEqual(1)
  expect(afterCatchup).toBeLessThanOrEqual(4)

  // Now the proof it STOPPED: wait past 3 full poll intervals (4s each). A
  // perpetual 4s poll would add ~3 more requests here; a stopped poll adds none.
  await page.waitForTimeout(13_000)
  expect(liveTailMaps).toBe(afterCatchup)

  // No control to press, no button: the page rendered and converged on its own.
  await expect(page.getByText('closed session fixture prompt').first()).toBeVisible()
})
