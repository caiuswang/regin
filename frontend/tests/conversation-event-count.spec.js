/**
 * The turn header's "N events" must equal the rows the spine actually renders.
 *
 * ConversationSpanCard is a v-if chain with no catch-all, so span families it
 * has no branch for (`turn`, `hook.*`, `cwd.*`, `session.title`, …) render
 * nothing. A header count computed from the raw descendant list therefore
 * advertised events the reader could not find — one real turn claimed 259 over
 * 61 rendered rows.
 *
 * `dispatch.js` is the single predicate both sides go through; this spec is
 * what keeps them honest, so a branch added to one and not the other fails
 * here instead of drifting quietly.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test('turn header count equals the rendered spine rows', async ({ page }) => {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  const at = i => `2026-05-08T10:00:${String(i).padStart(2, '0')}`

  // Three spans that render, four that the dispatcher has no branch for.
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'event count demo', is_test: true } },
    { trace_id: traceId, span_id: `r1-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'tool.Read', start_time: at(1),
      attributes: { tool_name: 'Read', file_path: '/repo/a.py', is_test: true } },
    { trace_id: traceId, span_id: `r2-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'tool.Grep', start_time: at(2),
      attributes: { tool_name: 'Grep', pattern: 'needle', is_test: true } },
    { trace_id: traceId, span_id: `r3-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'assistant.thinking', start_time: at(3), attributes: { is_test: true } },
    // Undispatched: must count for nothing and render nothing.
    { trace_id: traceId, span_id: `u1-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'turn', start_time: at(4), attributes: { is_test: true } },
    { trace_id: traceId, span_id: `u2-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'cwd.changed', start_time: at(5), attributes: { cwd: '/repo', is_test: true } },
    { trace_id: traceId, span_id: `u3-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'hook.stop_summary', start_time: at(6), attributes: { is_test: true } },
    { trace_id: traceId, span_id: `u4-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'session.title', start_time: at(7), attributes: { title: 'x', is_test: true } },
  ]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)

  const spine = page.locator('.event-spine')
  await expect(spine).toBeVisible({ timeout: 10_000 })

  const measured = await page.evaluate(() => {
    // The label shares its span with the `· ▾` disclosure glyph, so match
    // inside the string rather than requiring the whole node to be the count.
    const label = [...document.querySelectorAll('span')]
      .map(el => el.textContent.trim())
      .find(t => /^\d+ events? · [▴▾]$/.test(t))
    const rows = [...document.querySelectorAll('.event-spine-row')]
      .filter(el => getComputedStyle(el).display !== 'none')
    return { claimed: label ? parseInt(label, 10) : null, rendered: rows.length }
  })

  expect(measured.claimed, 'no "N events" label found on the turn header').not.toBeNull()
  expect(
    measured.claimed,
    `header claims ${measured.claimed} events but the spine rendered ${measured.rendered} rows`,
  ).toBe(measured.rendered)
  // ...and it is the dispatched subset, not the raw descendant list (7).
  expect(measured.rendered).toBe(3)
})
