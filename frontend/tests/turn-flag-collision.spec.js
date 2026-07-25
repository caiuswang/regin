/**
 * Turn flags on the Activity strip must stay readable when prompts land close
 * together in time.
 *
 * Positioning them purely by percent-of-duration means two prompts a few
 * seconds apart in a multi-hour session resolve to nearly the same offset, so
 * the pills stack and the one underneath hides the digit that is its entire
 * reason for existing. The fix de-collides in pixels, so this asserts on
 * measured rects — a percent-based check cannot see the overlap at all.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test('turn flags never overlap, even when prompts cluster in time', async ({ page }) => {
  const traceId = randomUUID()

  // Five prompts inside the first minute, then one far out to stretch the
  // trace window to ~12h. Percent-positioned, the first five all collapse
  // onto the left edge.
  const stamps = [
    '2026-05-07T10:00:00', '2026-05-07T10:00:04', '2026-05-07T10:00:09',
    '2026-05-07T10:00:14', '2026-05-07T10:00:20', '2026-05-07T22:00:00',
  ]
  const spans = stamps.map((ts, i) => ({
    trace_id: traceId, span_id: `p${i}-${traceId.slice(0, 8)}`, parent_id: null,
    name: 'prompt', start_time: ts,
    attributes: { text: `clustered prompt ${i + 1}`, is_test: true },
  }))

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  await page.goto(`/trace/sessions/${traceId}`)

  const flags = page.getByTestId('turn-flag')
  await expect(flags.first()).toBeVisible({ timeout: 10_000 })

  const rects = await page.evaluate(() => {
    const row = document.querySelector('[data-testid="turn-flag"]')?.parentElement
    return [...document.querySelectorAll('[data-testid="turn-flag"]')]
      .map(el => {
        const r = el.getBoundingClientRect()
        return { label: el.textContent.trim(), left: Math.round(r.left), right: Math.round(r.right) }
      })
      .sort((a, b) => a.left - b.left)
      .map(f => ({ ...f, rowRight: row ? Math.round(row.getBoundingClientRect().right) : null }))
  })

  expect(rects.length).toBeGreaterThan(1)

  const overlaps = []
  for (let i = 1; i < rects.length; i++) {
    if (rects[i].left < rects[i - 1].right) {
      overlaps.push(`${rects[i - 1].label}(→${rects[i - 1].right}) over ${rects[i].label}(${rects[i].left}→)`)
    }
  }
  expect(overlaps, `turn flags overlap and hide their numbers: ${overlaps.join('; ')}`).toEqual([])

  // De-colliding must not shove the tail off the end of the row either.
  const last = rects[rects.length - 1]
  expect(last.right, 'last turn flag pushed past the strip').toBeLessThanOrEqual(last.rowRight + 1)
})
