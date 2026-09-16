/**
 * SessionTraceView: the live tail must keep reconciling poll after poll.
 *
 * `reloadLiveTail` runs the card-span prune (`pruneCardSpans`) BEFORE the tail
 * reconcile, so anything that throws in the prune silently kills everything
 * downstream — the summary, the fresh-page reconcile, the subtree refresh and
 * the `newestLoadedId` advance. The view keeps rendering its initial load and
 * simply stops moving, which no existing spec noticed: the whole trace suite
 * asserts on a FIRST paint, and `vite build` reports an unresolved import as a
 * warning, not an error.
 *
 * So this drives the poll across several turns and asserts the feed actually
 * advances, with no page error. Data goes in via `/api/session-spans` with
 * `is_test=true`, so the trace stays out of the sessions list.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

// Server-local NAIVE, not UTC — the backend writes stamps with no zone and
// `parseLocalIso` reads them as local. A `toISOString()` stamp lands hours off
// the server clock and the session ages out of "active" before the poll runs.
function nowIso(offsetSec) {
  const tzOffsetMs = new Date().getTimezoneOffset() * 60_000
  return new Date(Date.now() - tzOffsetMs + offsetSec * 1000)
    .toISOString().replace('Z', '')
}

function promptSpan(traceId, n) {
  return {
    trace_id: traceId, span_id: `prompt-${traceId.slice(0, 8)}-${n}`,
    parent_id: null, name: 'prompt',
    start_time: nowIso(-30 + n), end_time: nowIso(-30 + n),
    status_code: 'OK',
    attributes: { text: `live tail fixture turn ${n}`, is_test: true },
  }
}

test('the live tail keeps advancing across polls', async ({ page }) => {
  const traceId = randomUUID()
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })

  await post(page, [promptSpan(traceId, 1)])
  await page.goto(`/trace/sessions/${traceId}`)
  await expect(page.getByText('live tail fixture turn 1').first())
    .toBeVisible({ timeout: 10_000 })

  // Two further turns, each asserted separately: the first proves one poll
  // completed past the prune, the second that the poll did not self-terminate
  // on a half-applied window.
  for (const n of [2, 3]) {
    await post(page, [promptSpan(traceId, n)])
    await expect(page.getByText(`live tail fixture turn ${n}`).first())
      .toBeVisible({ timeout: 20_000 })
  }

  expect(errors.join('\n')).toBe('')
})
