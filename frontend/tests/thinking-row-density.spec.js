/**
 * Thinking spans usually arrive with NO captured text — the transcript keeps
 * the fact that the model reasoned, not the reasoning. Rendering those as a
 * full bordered card with its own accent colour spent a whole block of
 * vertical rhythm, and its own colour on the spine, advertising a payload that
 * isn't there; a feed of them was mostly filler.
 *
 * So the shape is conditional: a text-bearing thinking span keeps the card, an
 * empty one collapses to a single muted line with a neutral spine node. Both
 * halves are asserted here — dropping the card for the text case would lose
 * real content, which is the opposite mistake.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test('empty thinking spans collapse to a muted line; text-bearing ones keep the card', async ({ page }) => {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  const emptyId = `th-empty-${traceId.slice(0, 8)}`
  const fullId = `th-full-${traceId.slice(0, 8)}`

  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-06T10:00:00', attributes: { text: 'thinking density demo', is_test: true } },
    { trace_id: traceId, span_id: emptyId, parent_id: prompt, name: 'assistant.thinking',
      start_time: '2026-05-06T10:00:01', end_time: '2026-05-06T10:00:04',
      attributes: { is_test: true } },
    { trace_id: traceId, span_id: fullId, parent_id: prompt, name: 'assistant.thinking',
      start_time: '2026-05-06T10:00:05', end_time: '2026-05-06T10:00:09',
      attributes: { thinking_text: 'Weighing two encodings before picking one.', is_test: true } },
  ]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  await page.evaluate(() => localStorage.setItem('regin_session_view_mode', 'conversation'))
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)

  // The text-bearing one still renders its content.
  await expect(page.getByText('Weighing two encodings before picking one.')).toBeVisible({ timeout: 10_000 })

  // Poll until both rows are mounted: under parallel load the spine can still
  // be rendering when the text assertion above first resolves, and a single
  // measurement then reads a half-built feed.
  const measure = () => page.evaluate(({ emptyId, fullId }) => {
    const rowFor = id => {
      const host = document.querySelector(`[data-span-id="${id}"]`)
      return host ? host.closest('.event-spine-row') || host : null
    }
    const read = el => {
      if (!el) return null
      const dot = el.querySelector('.spine-dot')
      return {
        h: Math.round(el.getBoundingClientRect().height),
        dot: dot ? getComputedStyle(dot).backgroundColor : null,
        text: el.textContent.trim().slice(0, 60),
      }
    }
    return { empty: read(rowFor(emptyId)), full: read(rowFor(fullId)) }
  }, { emptyId, fullId })

  let geometry = null
  await expect.poll(async () => {
    geometry = await measure()
    return !!(geometry?.empty && geometry?.full)
  }, { timeout: 15_000, message: 'thinking rows never mounted' }).toBe(true)

  expect(geometry.empty, 'empty thinking row not found').not.toBeNull()
  expect(geometry.full, 'text-bearing thinking row not found').not.toBeNull()

  // The empty one is a line, not a card. 34px is generous for one 12px line
  // plus padding, and well under the ~60px the card occupied.
  expect(
    geometry.empty.h,
    `empty thinking row still card-sized (${geometry.empty.h}px): ${geometry.empty.text}`,
  ).toBeLessThanOrEqual(34)
  // ...and it is visibly slimmer than the card, whatever the exact numbers.
  expect(geometry.empty.h).toBeLessThan(geometry.full.h)

  // Its spine node is neutral, not the thinking accent — an empty span should
  // not claim a content colour. Compared against the text-bearing row's own
  // dot rather than a colour literal: Tailwind v4 serialises these as
  // `oklch(...)`, so an `rgb(...)` literal is a string that can never match
  // and the assertion would be unfailable.
  expect(geometry.empty.dot, 'empty thinking dot missing').toBeTruthy()
  expect(geometry.full.dot, 'text-bearing thinking dot missing').toBeTruthy()
  expect(
    geometry.empty.dot,
    `empty thinking span still uses the content dot colour (${geometry.empty.dot})`,
  ).not.toBe(geometry.full.dot)
})
