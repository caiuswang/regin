/**
 * A thinking block that captured text is often the only place the model's
 * reasoning survives, and the card scrolls it inside a `max-h-72` box — so
 * hand-selecting it is exactly the case where a drag misses the tail. The
 * card therefore carries the shared Copy affordance, and copies the whole
 * `thinking_text`, not the visible slice.
 *
 * The empty half is asserted too: a thinking span with no captured text
 * collapses to a single muted line, and a Copy button there would offer to
 * copy nothing.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

// Long enough that the card's max-h-72 box scrolls, with a distinctive tail —
// a truncated copy would drop it.
const THINKING_TEXT = [
  'Weighing two encodings before picking one.',
  ...Array.from({ length: 40 }, (_, i) => `Consideration ${i + 1}: neither branch is free.`),
  'TAIL-MARKER-8f21',
].join('\n')

test('a text-bearing thinking card copies its full text; an empty one has no Copy', async ({ page }) => {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  const emptyId = `th-empty-${traceId.slice(0, 8)}`
  const fullId = `th-full-${traceId.slice(0, 8)}`

  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-06T10:00:00', attributes: { text: 'thinking copy demo', is_test: true } },
    // duration_ms is set explicitly: ingest does not derive it from the
    // timestamps, and virtually every real thinking span carries one — so
    // without it the card renders a two-child header the users never see.
    { trace_id: traceId, span_id: emptyId, parent_id: prompt, name: 'assistant.thinking',
      start_time: '2026-05-06T10:00:01', end_time: '2026-05-06T10:00:04', duration_ms: 3000,
      attributes: { is_test: true } },
    { trace_id: traceId, span_id: fullId, parent_id: prompt, name: 'assistant.thinking',
      start_time: '2026-05-06T10:00:05', end_time: '2026-05-06T10:00:09', duration_ms: 4000,
      attributes: { thinking_text: THINKING_TEXT, is_test: true } },
  ]

  // Capture what the card hands the clipboard. Stubbing writeText rather than
  // granting clipboard permissions keeps the assertion on the wiring (does the
  // button pass the whole attribute?) instead of on browser permission state.
  await page.addInitScript(() => {
    window.__copied = []
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (text) => { window.__copied.push(text) } },
    })
  })

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  await page.evaluate(() => localStorage.setItem('regin_session_view_mode', 'conversation'))
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)

  const fullRow = page.locator(`[data-span-id="${fullId}"]`)
  const emptyRow = page.locator(`[data-span-id="${emptyId}"]`)
  await expect(fullRow).toBeVisible({ timeout: 10_000 })
  await expect(emptyRow).toBeVisible()

  const copy = fullRow.getByRole('button', { name: 'Copy' })
  await expect(copy).toHaveCount(1)
  await expect(emptyRow.getByRole('button', { name: 'Copy' })).toHaveCount(0)

  // Hover-revealed, not always-on: the card's `group` hover is what fades it in.
  await expect(copy).toHaveCSS('opacity', '0')
  await fullRow.hover()
  await expect(copy).toHaveCSS('opacity', '1')

  // Header layout with a duration present (the shape ~99% of real thinking
  // spans have): Copy and the duration share the row, Copy first, and both
  // stay clear of the scrolling text box below.
  const duration = fullRow.getByText('4.0s')
  await expect(duration).toBeVisible()
  const [copyBox, durationBox] = await Promise.all([copy.boundingBox(), duration.boundingBox()])
  expect(copyBox.x + copyBox.width).toBeLessThanOrEqual(durationBox.x)
  expect(copyBox.y + copyBox.height).toBeLessThanOrEqual(
    await fullRow.locator('.overflow-y-auto').first().evaluate(el => el.getBoundingClientRect().top),
  )

  await copy.click()
  await expect(page.getByText('Copied!')).toBeVisible()
  expect(await page.evaluate(() => window.__copied)).toEqual([THINKING_TEXT])

  // The click belongs to the button, not the card: copying must not also
  // select the span and swap the detail panel out from under the reader.
  await expect(fullRow.locator('.event-selected')).toHaveCount(0)
})
