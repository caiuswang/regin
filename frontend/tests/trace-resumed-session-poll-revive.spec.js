/**
 * SessionTraceView: a session RESUMED after the view gave up on it must come
 * back to life — the Live pill flips and the self-terminated poll re-arms.
 *
 * The poll retires once an ended session's tail converges (see
 * trace-closed-session-poll-stop.spec.js). Before the fix, `ended_at` stayed
 * set forever after an exit→resume (the ingest upsert kept MAX(ended_at)),
 * so the view read the resumed session as ended: pill stuck on "Ended", poll
 * dead, scroll/wheel reload retired. The ingest now clears `ended_at` /
 * `ended_reason` when a genuine restart lands
 * (`_reconcile_restart_ended_marker`), and the view re-arms its poll on the
 * first reload that observes the cleared marker.
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

test('a resumed session flips back to Live and the poll re-arms', async ({ page }) => {
  const traceId = randomUUID()
  const short = traceId.slice(0, 8)

  // Seed: one settled prompt + a session.end span → the session is CLOSED
  // (ended_at set) before the view ever opens.
  await post(page, [
    {
      trace_id: traceId, span_id: `prompt-${short}`, parent_id: null, name: 'prompt',
      start_time: '2026-05-17T08:00:00.000000', end_time: '2026-05-17T08:00:01.000000',
      status_code: 'OK', attributes: { text: 'resumed session fixture prompt', is_test: true },
    },
    {
      trace_id: traceId, span_id: `end-${short}`, parent_id: null, name: 'session.end',
      start_time: '2026-05-17T08:00:05.000000', end_time: '2026-05-17T08:00:05.000000',
      status_code: 'OK', attributes: { reason: 'exit', is_test: true },
    },
  ])

  let liveTailMaps = 0
  page.on('request', (req) => {
    if (req.method() === 'GET' && isLiveTailMap(req.url(), traceId)) liveTailMaps += 1
  })

  await page.goto(`/trace/sessions/${traceId}`)
  await expect(page.getByText('resumed session fixture prompt').first())
    .toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('Ended', { exact: true }).first())
    .toBeVisible({ timeout: 10_000 })

  // Let the bounded catch-up settle, then prove the poll has STOPPED before
  // the resume — otherwise the revival below proves nothing.
  await page.waitForTimeout(3_000)
  const afterCatchup = liveTailMaps
  await page.waitForTimeout(9_000)
  expect(liveTailMaps).toBe(afterCatchup)

  // The resume: a session.start newer than the end plus real work in the
  // same batch — a genuine restart, so the ingest clears ended_at.
  await post(page, [
    {
      trace_id: traceId, span_id: `start2-${short}`, parent_id: null, name: 'session.start',
      start_time: '2026-05-18T09:00:00.000000', end_time: '2026-05-18T09:00:00.000000',
      status_code: 'OK', attributes: { cwd: '/repo', source: 'resume', is_test: true },
    },
    {
      trace_id: traceId, span_id: `prompt2-${short}`, parent_id: null, name: 'prompt',
      start_time: '2026-05-18T09:00:01.000000', end_time: '2026-05-18T09:00:02.000000',
      status_code: 'OK', attributes: { text: 'keep going after resume', is_test: true },
    },
  ])

  // No poll is running, so nothing fetches the flip on its own — the header
  // Reload is the observation point that must re-arm the view.
  const beforeManualReload = liveTailMaps
  await page.getByRole('button', { name: 'Reload' }).first().click()
  await expect(page.getByText('Live', { exact: true }).first())
    .toBeVisible({ timeout: 10_000 })

  // The poll is back: beyond the manual reload's own fetch, further
  // live-tail map requests arrive on the 4s tick.
  await expect.poll(() => liveTailMaps, { timeout: 12_000 })
    .toBeGreaterThanOrEqual(beforeManualReload + 2)
})
