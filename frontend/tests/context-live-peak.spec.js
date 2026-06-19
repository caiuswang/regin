/**
 * Headline ctx% tracks the LIVE context peak (post-/compact), not the
 * all-time high-water mark.
 *
 * Before this fix the header divided the all-time `peak_main_context_tokens`
 * by the window, so a session that hit 90% and then `/compact`-ed back down
 * stayed pinned at "ctx 90%" forever — the accumulated number never dropped.
 * Now the headline divides `live_context_tokens` (the main-flow peak SINCE
 * the last `compact.post`), and the pre-compaction high is demoted to a muted
 * "peaked N%" chip so the reset reads as a reset, not lost data.
 *
 * Portable: bootstraps its own synthetic boundary (is_test=true) via
 * `/api/session-spans` + the bracketing turns via `/api/turn-usage`. Model is
 * left unset → window infers to 200k, so 42k→21% live, 180k→90% peak.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test('headline ctx% reflects live peak after compaction, with a peaked chip', async ({ page }) => {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`

  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-03T10:00:00', attributes: { text: 'live-peak demo', is_test: true } },
    { trace_id: traceId, span_id: `post-${traceId.slice(0, 8)}`, parent_id: null, name: 'compact.post',
      start_time: '2026-05-03T10:05:30', attributes: { trigger: 'manual', summary: 'compacted', is_test: true } },
  ]
  // Pre-compaction turn peaks at 180k (90% of an inferred 200k window); the
  // post-compaction turn resets to 42k (21%).
  const turns = [
    { trace_id: traceId, turn_uuid: `t1-${traceId.slice(0, 8)}`,
      timestamp: '2026-05-03T10:04:00', context_used_tokens: 180000 },
    { trace_id: traceId, turn_uuid: `t2-${traceId.slice(0, 8)}`,
      timestamp: '2026-05-03T10:06:00', context_used_tokens: 42000 },
  ]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  const headers = { Authorization: `Bearer ${token}` }

  expect((await page.request.post('/api/session-spans', { headers, data: spans })).ok()).toBeTruthy()
  expect((await page.request.post('/api/turn-usage', { headers, data: turns })).ok()).toBeTruthy()

  await page.goto(`/trace/sessions/${traceId}`)

  // Headline is the live peak (21%), NOT the pre-compaction 90%.
  await expect(page.getByText(/ctx 21%/)).toBeVisible({ timeout: 10_000 })
  // The pre-compaction high is surfaced as a muted "peaked" chip.
  await expect(page.getByText(/peaked 90%/)).toBeVisible()

  await page.screenshot({ path: '/tmp/regin-verify/context-live-peak.png', fullPage: true })
})
