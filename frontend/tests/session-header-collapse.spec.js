/**
 * Collapsible trace header (redesign): the full header folds into one compact
 * row — status dot, title, mono digest, view switcher, Reload — once the body
 * scrolls past the header's own height, and re-expands back at the top. The
 * Details button / H key pin the choice until the scroll returns to the top.
 *
 * Scrolls are driven with real wheel input: the state machine deliberately
 * ignores layout-driven scrolls (scroll anchoring, programmatic scrollTop
 * writes) so it can't oscillate, and plain JS scrolls must no-op here.
 *
 * Asserted through the DOM, not screenshots: the digest row appears exactly
 * when the vitals strip folds away, and the pin survives a mid-page scroll.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

async function seedScrollableSession(page) {
  const traceId = randomUUID()
  const spans = []
  // Long assistant bodies so the page stays tall even after the header folds.
  const filler = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. '.repeat(30)
  // 20 roots stays under the /map page size (50), so "load older" never
  // fires at the top and yanks the scroll back down mid-assertion.
  for (let i = 0; i < 20; i++) {
    const pid = `p-${traceId.slice(0, 8)}-${i}`
    spans.push({
      trace_id: traceId,
      span_id: pid,
      parent_id: null,
      name: 'prompt',
      start_time: `2026-05-09T10:${String(i).padStart(2, '0')}:00`,
      attributes: { text: `prompt number ${i} — padding the feed so the page scrolls`, is_test: true },
    })
    spans.push({
      trace_id: traceId,
      span_id: `a-${traceId.slice(0, 8)}-${i}`,
      parent_id: pid,
      name: 'assistant_response',
      start_time: `2026-05-09T10:${String(i).padStart(2, '0')}:05`,
      attributes: { text: filler, is_test: true },
    })
  }
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  return traceId
}

async function wheel(page, deltaY) {
  // The right gutter is .content-scroll's own padding strip: no span card
  // (some have internal scroll + overscroll containment) can swallow the
  // wheel before it reaches the page scroller.
  await page.mouse.move(1420, 450)
  await page.mouse.wheel(0, deltaY)
}

test('header auto-collapses past its own height, re-expands at the top, pin survives mid-page scroll', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  const digest = page.getByTestId('trace-header-digest')
  const toggle = page.getByTestId('header-details-toggle')

  // Expanded at the top: vitals visible, no digest, toggle offers to hide.
  await expect(vitals).toBeVisible({ timeout: 10_000 })
  await expect(digest).toBeHidden()
  await expect(toggle).toContainText('Hide details')

  // A programmatic scrollTop write is NOT user input — nothing may react.
  await page.evaluate(() => { document.querySelector('.content-scroll').scrollTop = 1500 })
  await page.waitForTimeout(400)
  await expect(digest).toHaveCount(0)
  await expect(vitals).toBeVisible()

  // Scrolling past the header's own height collapses it (the threshold is
  // the expanded header's measured height, floored at 72px — see the view).
  await wheel(page, 1500)
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()
  await expect(toggle).toContainText('Details')

  // Deep in the feed the stuck header's background must cover the
  // scrollport's top edge — scrolled content bleeding through above it was
  // the -top/pt regression. (Shallow scrolls can't assert this: before the
  // header reaches its sticky pin, the page chrome legitimately sits above
  // it.)
  await wheel(page, 3000)
  await expect(digest).toBeVisible()
  const covered = await page.evaluate(() => {
    const scroller = document.querySelector('.content-scroll')
    const header = document.querySelector('[data-testid="trace-sticky-header"]')
    if (!scroller || !header) return null
    const y = scroller.getBoundingClientRect().top + 2
    const el = document.elementFromPoint(window.innerWidth / 2, y)
    return el ? header.contains(el) : false
  })
  expect(covered, 'scrolled content bleeds through above the collapsed sticky header').toBe(true)

  // Back at the top it re-expands on its own (after a short dwell).
  await wheel(page, -99999)
  await expect.poll(() => page.evaluate(() =>
    Math.round(document.querySelector('.content-scroll').scrollTop)),
    { message: 'scroller actually reached the top' }).toBeLessThan(24)
  await expect(vitals).toBeVisible()
  await expect(digest).toBeHidden()

  // Manual pin: collapse via the button, scroll mid-page — stays collapsed.
  await toggle.click()
  await expect(digest).toBeVisible()
  await wheel(page, 1500)
  await page.waitForTimeout(200)
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()

  // Returning to the top clears the pin and re-expands.
  await wheel(page, -99999)
  await expect(vitals).toBeVisible()

  // H is the keyboard twin of the button.
  await page.keyboard.press('h')
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()
  await page.keyboard.press('H')
  await expect(vitals).toBeVisible()
  await expect(digest).toBeHidden()
})

test('below lg the header never auto-collapses — it scrolls away whole instead', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  await expect(vitals).toBeVisible({ timeout: 10_000 })

  // On a phone the full header isn't sticky — it scrolls off with the page
  // and the compact bar pins. Folding it on scroll would only unmount the
  // spend panel / strips out from under the reader (spend-panel-scroll.spec).
  await wheel(page, 800)
  await page.waitForTimeout(300)
  await expect(page.getByTestId('trace-header-digest')).toHaveCount(0)
  await expect(vitals).toBeAttached()
})
