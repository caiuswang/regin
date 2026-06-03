/**
 * Compaction reclaim delta chip.
 *
 * A `/compact` carries no token payload on its boundary spans, but
 * turn_usage records `context_used_tokens` per turn. The serve-time
 * derivation (`queries.py _attach_compaction_reclaim`) stamps
 * `attributes.reclaimed_tokens` onto `compact.post` = context used by the
 * last turn BEFORE `compact.pre` minus the first turn AFTER `compact.post`,
 * and both render paths show it as a `· freed ~N` suffix.
 *
 * Portable: bootstraps its own synthetic boundary (is_test=true) via
 * `/api/session-spans` + the bracketing turns via `/api/turn-usage`.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test('compact.post shows the reclaimed-tokens delta', async ({ page }) => {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`

  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-03T10:00:00', attributes: { text: 'compaction demo', is_test: true } },
    // Compaction boundaries are conversational ROOTS (parent_id=null), not
    // prompt children — useSpanTree only promotes a compact.* span to a
    // rendered divider when its parent isn't in the span set.
    { trace_id: traceId, span_id: `pre-${traceId.slice(0, 8)}`, parent_id: null, name: 'compact.pre',
      start_time: '2026-05-03T10:05:00', attributes: { trigger: 'auto', is_test: true } },
    { trace_id: traceId, span_id: `post-${traceId.slice(0, 8)}`, parent_id: null, name: 'compact.post',
      start_time: '2026-05-03T10:05:30', attributes: { trigger: 'auto', summary: 'compacted summary', is_test: true } },
  ]
  // reclaimed = 180000 (last turn before pre) - 42000 (first turn after post)
  //           = 138000 → fmtTokens → "138k"
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

  await page.evaluate(() => localStorage.setItem('regin_session_view_mode', 'conversation'))
  await page.goto(`/trace/sessions/${traceId}`)

  await expect(page.getByText(/freed ~138k/)).toBeVisible({ timeout: 10_000 })

  await page.screenshot({ path: '/tmp/regin-verify/compaction-reclaim.png', fullPage: true })
})
