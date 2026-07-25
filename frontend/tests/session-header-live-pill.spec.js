/**
 * The Live/Ended pill must sit centred on the session title's FIRST line.
 *
 * This has drifted twice. `items-baseline` looks like the principled choice
 * and is wrong: the pill is a bordered box whose padding hangs below its text
 * baseline, so baseline alignment drops it ~6px under the title's optical
 * centre. `items-start` plus a nudge of (title line-height - pill height) / 2
 * is what actually centres it, and it keeps working when a long title wraps —
 * the pill stays on the first line rather than centring against the whole
 * block.
 *
 * Asserted in measured pixels, because "looks aligned" is exactly the judgement
 * that got it wrong twice.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const MAX_OFFSET = 1.5

async function seedLiveSession(page, title) {
  const traceId = randomUUID()
  // No `session.end` span → the session reads as live, so the pill says "Live".
  const spans = [
    { trace_id: traceId, span_id: `p-${traceId.slice(0, 8)}`, parent_id: null, name: 'prompt',
      start_time: '2026-05-09T10:00:00', attributes: { text: title, is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  return traceId
}

// Polls rather than measuring once: under parallel load the header can still
// be mounting when the "Live" text first resolves, and a single measurement
// then reads a half-built row. Returns null until the row is measurable.
async function measurePill(page) {
  let last = null
  await expect.poll(async () => {
    last = await measurePillOnce(page)
    return last !== null
  }, { timeout: 15_000, message: 'header never produced a measurable Live pill + title row' }).toBe(true)
  return last
}

async function measurePillOnce(page) {
  return page.evaluate(() => {
    const root = document.querySelector('[data-component="src/components/SessionTraceHeader.vue"]')
      || document
    const pill = [...root.querySelectorAll('span')]
      .find(s => /^(Live|Ended)$/.test(s.textContent.trim()))
    if (!pill) return null
    const row = pill.parentElement
    const h1 = row.querySelector('h1')
    if (!h1) return null
    // The title's FIRST LINE box. Must come from a Range over the h1's
    // contents: the h1 is a block, so `h1.getClientRects()` returns one rect
    // for the whole element and silently reports a wrapped title as one line.
    const range = document.createRange()
    range.selectNodeContents(h1)
    const rects = range.getClientRects()
    const line = rects[0]
    if (!line) return null
    const p = pill.getBoundingClientRect()
    return {
      text: pill.textContent.trim(),
      offset: +(((p.top + p.bottom) / 2) - ((line.top + line.bottom) / 2)).toFixed(2),
      lines: rects.length,
      lineH: +line.height.toFixed(1),
    }
  })
}

test('Live pill stays centred on the title first line, short and wrapping titles', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 940 })

  const shortId = await seedLiveSession(page, 'short title')
  await page.goto(`/trace/sessions/${shortId}`)
  await expect(page.getByText('Live', { exact: true }).first()).toBeVisible({ timeout: 10_000 })

  const short = await measurePill(page)
  expect(short, 'no Live pill / title found in the header').not.toBeNull()
  expect(
    Math.abs(short.offset),
    `Live pill is ${short.offset}px off the title's first-line centre (short title)`,
  ).toBeLessThanOrEqual(MAX_OFFSET)

  // A wrapping title must not drag the pill down to the block's centre.
  const longId = await seedLiveSession(page, `wrapping ${'title '.repeat(24)}end`)
  await page.goto(`/trace/sessions/${longId}`)
  await expect(page.getByText('Live', { exact: true }).first()).toBeVisible({ timeout: 10_000 })

  const long = await measurePill(page)
  expect(long, 'no Live pill / title found for the wrapping title').not.toBeNull()
  expect(
    Math.abs(long.offset),
    `Live pill is ${long.offset}px off the FIRST line centre across ${long.lines} title lines`,
  ).toBeLessThanOrEqual(MAX_OFFSET)
})
