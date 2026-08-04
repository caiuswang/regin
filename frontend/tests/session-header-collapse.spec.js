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
  // wheel before it reaches the page scroller. Derived from the live viewport,
  // never hard-coded — a fixed desktop x lands outside a phone viewport, the
  // wheel hit-tests to nothing, and every "it didn't collapse" assertion below
  // passes for the wrong reason.
  const vp = page.viewportSize()
  await page.mouse.move(vp.width - 20, Math.round(vp.height / 2))
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

test('a fold pinned inside the top band survives a layout-driven scroll nudge', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const digest = page.getByTestId('trace-header-digest')
  await expect(page.getByTestId('trace-vitals-strip')).toBeVisible({ timeout: 10_000 })

  // Fold a few px off the top — inside the 24px band whose own rule is "at the
  // top ⇒ clear the pin". Without a guard the pin is unlocked the instant it
  // is created, and the next nudge (a live poll's layout shift, written here
  // directly) pops the header back open under the reader.
  await page.evaluate(() => { document.querySelector('.content-scroll').scrollTop = 20 })
  await page.waitForTimeout(300)
  await page.getByTestId('header-details-toggle').click()
  await expect(digest).toBeVisible()

  await page.evaluate(() => { document.querySelector('.content-scroll').scrollTop = 0 })
  await page.waitForTimeout(400)
  await expect(digest).toBeVisible()

  // Leaving the band and coming back is the real unlock, and still works.
  await wheel(page, 1500)
  await expect(digest).toBeVisible()
  await wheel(page, -99999)
  await expect(page.getByTestId('trace-vitals-strip')).toBeVisible()
  await expect(digest).toBeHidden()
})

test('a viewport crossing under lg undoes an AUTO collapse but not a pinned one', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  const digest = page.getByTestId('trace-header-digest')
  await expect(vitals).toBeVisible({ timeout: 10_000 })

  // Below lg the header isn't sticky, so a fold that survived the resize would
  // carry its own Details button off-screen — the strips must return unasked.
  await wheel(page, 1500)
  await expect(digest).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(vitals).toBeVisible()
  await expect(digest).toHaveCount(0)

  // A deliberate fold is the user's call at any width, so it survives.
  // Let the wheel's 1200ms input window lapse before crossing back up: the
  // resize reflow fires a layout scroll from the still-deep offset, and if
  // the wheel above is still "recent input" it re-engages the AUTO fold
  // between the vitals assertion and the H below — turning that press into
  // an expand and inverting every assertion after it (trace-proven under
  // parallel load, where this whole test compresses to ~200ms).
  await page.waitForTimeout(1300)
  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(vitals).toBeVisible()
  await page.keyboard.press('h')
  await expect(digest).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(300)
  await expect(digest).toBeVisible()
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

// The expanded title column is `flex-1` beside a ~500-600px intrinsic actions
// column. With a basis of 0 it never overflowed the header's own flex line, so
// instead of wrapping the actions onto a second row the title shrank — to 41px
// at 1024px, where a 22-character title rendered as a 0×630px column of single
// characters. A zero-width h1 also reads as hidden, so it took
// `trace-agent-pane.spec.js`'s <xl deep-link case down with it.
//
// The bar is deliberately stated for a SHORT title on a session with no
// subagents (the narrower actions column): a long title beside the extra
// Agents button legitimately wraps to several lines at some widths.
test('a short expanded title keeps a readable column below xl', async ({ page }) => {
  // A short, fixed title so the geometry bar is about the COLUMN width, not
  // about how many lines this particular string happens to need.
  const traceId = randomUUID()
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` },
    data: [{
      trace_id: traceId, span_id: `p-${traceId.slice(0, 8)}`, parent_id: null,
      name: 'prompt', start_time: '2026-05-09T10:00:00',
      attributes: { text: 'header width fixture', is_test: true },
    }],
  })).ok()).toBeTruthy()

  for (const width of [1024, 1152, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`/trace/sessions/${traceId}`)
    const title = page.locator('header h1.text-2xl')
    await expect(title, `expanded title must render at ${width}px`).toBeVisible({ timeout: 10_000 })
    const box = await title.boundingBox()
    expect(box.width, `title must keep a readable width at ${width}px`).toBeGreaterThan(240)
    expect(box.height, `a 20-char title must fit one line at ${width}px`).toBeLessThan(40)
  }
})
