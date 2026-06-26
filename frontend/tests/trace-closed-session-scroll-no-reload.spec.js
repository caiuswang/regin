/**
 * SessionTraceView: scrolling to the END of a CLOSED session must NOT reload.
 *
 * The prior `perf(trace): self-terminating live-sync` work stopped the 4s timer
 * poll for an ended session, but left the scroll/wheel pull-to-refresh in
 * `useTraceScroll` ungated — so scrolling to the bottom of a closed session
 * still fired `reloadLiveTail()` and its backend transcript rescan. Once
 * live-sync has self-terminated (`liveSyncActive` false), the bottom-edge
 * affordances must be inert; the top-edge load-older must still work.
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

// Drive the document/container scroll listeners directly: jam every scroller to
// its bottom and dispatch the scroll + wheel-down events `useTraceScroll`
// listens for in capture phase. Synthetic events make the test independent of
// whether the fixture actually overflows the viewport.
async function pokeBottomEdge(page) {
  await page.evaluate(() => {
    const scrollers = [
      document.scrollingElement || document.documentElement,
      ...document.querySelectorAll('.content-scroll'),
    ].filter(Boolean)
    for (const s of scrollers) {
      s.scrollTop = s.scrollHeight
      s.dispatchEvent(new Event('scroll', { bubbles: true }))
      s.dispatchEvent(new WheelEvent('wheel', { deltaY: 120, bubbles: true }))
    }
    document.dispatchEvent(new Event('scroll'))
  })
}

test('a CLOSED session does not reload when scrolled to the bottom', async ({ page }) => {
  const traceId = randomUUID()
  const promptId = `prompt-${traceId.slice(0, 8)}`
  const endId = `end-${traceId.slice(0, 8)}`

  // Seed: one settled prompt + a session.end span → CLOSED before the view opens.
  await post(page, [
    {
      trace_id: traceId, span_id: promptId, parent_id: null, name: 'prompt',
      start_time: '2026-05-17T08:00:00.000000', end_time: '2026-05-17T08:00:01.000000',
      status_code: 'OK', attributes: { text: 'closed scroll fixture prompt', is_test: true },
    },
    {
      trace_id: traceId, span_id: endId, parent_id: null, name: 'session.end',
      start_time: '2026-05-17T08:00:05.000000', end_time: '2026-05-17T08:00:05.000000',
      status_code: 'OK', attributes: { reason: 'clear', is_test: true },
    },
  ])

  let liveTailMaps = 0
  page.on('request', (req) => {
    if (req.method() === 'GET' && isLiveTailMap(req.url(), traceId)) liveTailMaps += 1
  })

  await page.goto(`/trace/sessions/${traceId}`)
  await expect(page.getByText('closed scroll fixture prompt').first())
    .toBeVisible({ timeout: 10_000 })

  // Wait until live-sync has actually self-terminated: the live-tail map count
  // stops growing for a full poll interval (the timer poll has stopped and
  // `liveSyncActive` has flipped false). `ended_at` lands asynchronously, so a
  // freshly-closed session converges via the poll path — wait for it rather
  // than assuming termination at a fixed time.
  let afterCatchup = liveTailMaps
  await expect.poll(async () => {
    const before = liveTailMaps
    await page.waitForTimeout(5_000)
    afterCatchup = liveTailMaps
    return liveTailMaps - before  // 0 once polling has stopped
  }, { timeout: 30_000, intervals: [0] }).toBe(0)
  expect(afterCatchup).toBeGreaterThanOrEqual(1)

  // Scroll to the end repeatedly — the reported bug. Each poke would, before the
  // fix, fire a fresh live-tail map fetch. With live-sync retired it must not.
  for (let i = 0; i < 4; i++) {
    await pokeBottomEdge(page)
    await page.waitForTimeout(400)
  }
  await page.waitForTimeout(1_000)
  expect(liveTailMaps).toBe(afterCatchup)
})

test('a LIVE session still reloads when scrolled to the bottom', async ({ page }) => {
  const traceId = randomUUID()
  const promptId = `prompt-${traceId.slice(0, 8)}`

  // Seed: one settled prompt, NO session.end → the session stays LIVE.
  await post(page, [
    {
      trace_id: traceId, span_id: promptId, parent_id: null, name: 'prompt',
      start_time: '2026-05-17T08:00:00.000000', end_time: '2026-05-17T08:00:01.000000',
      status_code: 'OK', attributes: { text: 'live scroll fixture prompt', is_test: true },
    },
  ])

  let liveTailMaps = 0
  page.on('request', (req) => {
    if (req.method() === 'GET' && isLiveTailMap(req.url(), traceId)) liveTailMaps += 1
  })

  await page.goto(`/trace/sessions/${traceId}`)
  await expect(page.getByText('live scroll fixture prompt').first())
    .toBeVisible({ timeout: 10_000 })

  // Snapshot right after load, BEFORE the first 4s poll tick, so the increment
  // we measure is attributable to the scroll gesture, not the timer.
  await page.waitForTimeout(500)
  const beforeScroll = liveTailMaps
  await pokeBottomEdge(page)
  // The scroll-driven reloadLiveTail must fire promptly (well under the 4s poll).
  await expect.poll(() => liveTailMaps, { timeout: 2_500 }).toBeGreaterThan(beforeScroll)
})
