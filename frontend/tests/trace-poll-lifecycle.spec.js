/**
 * The live poll's cost and its lifecycle.
 *
 * Two defects this pins, both measured off `~/.local/share/regin/logs`:
 *
 *  - Every reconcile fired six requests — `/map`, `tool-rollup`, `turn-usage`
 *    and three `children?deep=1` — whether or not the tail had moved. Five of
 *    them re-fetched data derived from a tail that had not changed.
 *  - The poll only ever stopped on `ended_at`, which the SessionEnd hook
 *    writes. A session killed or closed without one polled for as long as its
 *    tab stayed open: 630 requests a minute against a session dead since the
 *    previous day.
 *
 * The seeded session below is exactly that shape — spans, no `ended_at`, and
 * nothing ever appending to it.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const POLL_MS = 4000
const QUIET_TICKS_BEFORE_SUSPEND = 5

function spansFor(traceId, { suffix = 'a', at = '2026-05-08T12:00:00' } = {}) {
  const p8 = `${traceId.slice(0, 8)}-${suffix}`
  return [
    { trace_id: traceId, span_id: `p-${p8}`, parent_id: null, name: 'prompt',
      start_time: at, attributes: { text: `turn ${suffix}`, is_test: true } },
    { trace_id: traceId, span_id: `b-${p8}`, parent_id: `p-${p8}`, name: 'tool.Bash',
      start_time: at, duration_ms: 100,
      attributes: { tool_name: 'Bash', command: `echo ${suffix}`, is_test: true } },
  ]
}

async function ingest(page, spans) {
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  const res = await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })
  expect(res.ok()).toBeTruthy()
}

/** Counts of the four requests a reconcile can make, since the last reset. */
function watchRequests(page) {
  const seen = { map: 0, children: 0, rollup: 0, turns: 0 }
  page.on('request', (r) => {
    const u = r.url()
    if (!u.includes('/api/sessions/')) return
    if (u.includes('/map?')) seen.map += 1
    else if (u.includes('children?deep=1')) seen.children += 1
    else if (u.endsWith('/tool-rollup')) seen.rollup += 1
    else if (u.endsWith('/turn-usage')) seen.turns += 1
  })
  return {
    seen,
    reset() { for (const k of Object.keys(seen)) seen[k] = 0 },
  }
}

/** How many live-poll intervals the page is holding. */
const pollTimerCount = (page) => page.evaluate(
  (ms) => [...(window.__intervals || new Map()).values()].filter(v => v === ms).length,
  POLL_MS,
)

async function countIntervals(page) {
  await page.addInitScript(() => {
    window.__intervals = new Map()
    const set = window.setInterval
    const clear = window.clearInterval
    window.setInterval = function (fn, ms, ...rest) {
      const id = set.call(window, fn, ms, ...rest)
      window.__intervals.set(id, ms)
      return id
    }
    window.clearInterval = function (id) {
      window.__intervals.delete(id)
      return clear.call(window, id)
    }
  })
}

/** Seed a live session (no `ended_at`) and park the reader on it. */
async function openLiveSession(page) {
  const traceId = randomUUID()
  await page.goto('/trace/sessions')
  await ingest(page, spansFor(traceId))
  await page.goto(`/trace/sessions/${traceId}`)
  await expect(page.getByText('turn a').first()).toBeVisible()
  // Let the initial load and its first reconcile settle before measuring.
  await page.waitForTimeout(POLL_MS * 2)
  return traceId
}

test('a tick whose tail did not move costs one /map and nothing else', async ({ page }) => {
  test.setTimeout(60_000)
  const watch = watchRequests(page)
  const traceId = await openLiveSession(page)

  watch.reset()
  await page.waitForTimeout(POLL_MS * 2.5)
  expect(watch.seen.map).toBeGreaterThanOrEqual(2)
  expect(watch.seen.children).toBe(0)
  expect(watch.seen.rollup).toBe(0)
  expect(watch.seen.turns).toBe(0)

  // A span lands: the very next reconcile owes the full set again.
  watch.reset()
  await ingest(page, spansFor(traceId, { suffix: 'b', at: '2026-05-08T12:05:00' }))
  await expect(page.getByText('turn b').first()).toBeVisible({ timeout: 15_000 })
  expect(watch.seen.rollup).toBeGreaterThanOrEqual(1)
  expect(watch.seen.children).toBeGreaterThanOrEqual(1)
})

test('the poll suspends on a session that stops changing, and a span wakes it', async ({ page }) => {
  test.setTimeout(120_000)
  await countIntervals(page)
  const watch = watchRequests(page)
  const traceId = await openLiveSession(page)

  // Quiet for longer than the budget: the timer is retired, not merely idle.
  await expect.poll(
    () => pollTimerCount(page),
    { timeout: POLL_MS * (QUIET_TICKS_BEFORE_SUSPEND + 4), intervals: [1000] },
  ).toBe(0)

  watch.reset()
  await page.waitForTimeout(POLL_MS * 2)
  expect(watch.seen.map).toBe(0)

  // The ingest's own frame is the way back — nobody scrolls, nobody clicks.
  await ingest(page, spansFor(traceId, { suffix: 'c', at: '2026-05-08T12:10:00' }))
  await expect(page.getByText('turn c').first()).toBeVisible({ timeout: 20_000 })
  expect(await pollTimerCount(page)).toBe(1)
})

test('Refresh resumes a suspended poll', async ({ page }) => {
  test.setTimeout(120_000)
  await countIntervals(page)
  await openLiveSession(page)

  await expect.poll(
    () => pollTimerCount(page),
    { timeout: POLL_MS * (QUIET_TICKS_BEFORE_SUSPEND + 4), intervals: [1000] },
  ).toBe(0)

  await page.getByTitle(/Re-fetch spans/).first().click()
  await expect.poll(() => pollTimerCount(page), { timeout: 10_000 }).toBe(1)
})

test('switching sessions mid-load leaves exactly one poll timer', async ({ page }) => {
  test.setTimeout(120_000)
  await countIntervals(page)
  const a = await openLiveSession(page)
  const b = randomUUID()
  await ingest(page, spansFor(b, { suffix: 'b2' }))

  // Switch before the initial load can finish. Each of these used to leave the
  // pending `runInitialLoad` to arm a timer after its own instance was gone —
  // and because `reload()` reads the LIVE route, every orphan then polled
  // whichever session was open next.
  for (let i = 0; i < 8; i += 1) {
    await page.goto(`/trace/sessions/${i % 2 ? a : b}`)
    await page.waitForTimeout(150)
  }
  await page.goto(`/trace/sessions/${a}`)
  await expect(page.getByText('turn a').first()).toBeVisible()
  await page.waitForTimeout(POLL_MS)
  expect(await pollTimerCount(page)).toBeLessThanOrEqual(1)
})

/**
 * The first-open `turn-usage` fetch carries the counters the conversation
 * feed's footers and TOC render, and NOT each turn's `span_refs` /
 * `tool_summary` — those are the turns sidebar's, and it lives behind the
 * detail rail, which is closed on a fresh conversation-tab load. On a
 * 223-turn session that was 86KB of a 180KB response.
 */
test('turn detail is fetched when the rail opens, not on first load', async ({ page }) => {
  test.setTimeout(60_000)
  const urls = []
  page.on('request', (r) => {
    if (r.url().includes('/turn-usage')) urls.push(new URL(r.url()).search)
  })

  const traceId = randomUUID()
  await page.goto('/trace/sessions')
  await ingest(page, spansFor(traceId))
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/turn-usage', {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      trace_id: traceId,
      turns: [{
        turn_uuid: `t-${traceId.slice(0, 8)}`, turn_index: 0,
        timestamp: '2026-05-08T12:00:05+00:00', model: 'claude-opus-4-7',
        input_tokens: 10, output_tokens: 5,
        cache_read_tokens: 0, cache_creation_tokens: 0,
        context_used_tokens: 15,
      }],
    },
  })).ok()).toBeTruthy()

  await page.goto(`/trace/sessions/${traceId}`)
  await expect(page.getByText('turn a').first()).toBeVisible()
  await expect.poll(() => urls.length, { timeout: 15_000 }).toBeGreaterThan(0)
  // Everything the feed needed, none of what only the sidebar reads.
  expect(urls.every(s => !s.includes('detail=1'))).toBe(true)

  // Opening the rail is what makes those fields worth their bytes.
  urls.length = 0
  await page.getByText('turn a').first().click()
  // Exact label: the header carries a "Hide details" toggle that a loose
  // /details/i would match first, and it does not open the rail.
  await page.getByRole('button', { name: 'Show span details' }).click()
  await expect.poll(() => urls.filter(s => s.includes('detail=1')).length,
    { timeout: 15_000 }).toBeGreaterThan(0)
})
