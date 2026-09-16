/**
 * Collapsible trace header (redesign): the full header folds into one compact
 * row — status dot, title, mono digest, view switcher, Reload — once the body
 * scrolls past the header's own height. Getting it BACK is asymmetric: a
 * scroll-up never reveals, however far it travels — that takes a sustained
 * pull past the top worth PULL_NEED (420px) at PER_EVENT_CAP (26px) an event,
 * or the Details button / H key, which bypass the gesture and pin the choice.
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
import { setTraceScrollTop, traceScrollTop, wheelOverTrace } from './helpers/traceScroller.js'

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

// Derived from the live scroll region, never hard-coded — a fixed desktop x
// lands outside a phone viewport (or, once the transcript scrolls inside the
// view, on the frozen shell beside it), the wheel hit-tests to nothing, and
// every "it didn't collapse" assertion below passes for the wrong reason.
const wheel = wheelOverTrace

// The reveal gesture. 420px of credited pull at 26px an event is ~17 events;
// 24 leaves room for the handful spent while the dwell gate is still closing.
// Each -60px delta is deliberately larger than the cap — a bigger wheel notch
// must not buy a faster reveal, which is the whole point of the cap.
const pull = (page) => wheel(page, -60, 24)

// Read straight off the imperative write. The fill is never in reactive state,
// so there is no component to interrogate — the inline transform IS the value.
const meterFill = (page) => page.evaluate(() => {
  const bar = document.querySelector('[data-header-pull-bar]')
  const m = /scaleX\(([0-9.]+)\)/.exec(bar?.style.transform || '')
  return m ? Number(m[1]) : 0
})

test('header auto-collapses past its own height, resists a scroll-up, yields to a pull', async ({ page }) => {
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
  await setTraceScrollTop(page, 1500)
  await page.waitForTimeout(400)
  await expect(digest).toHaveCount(0)
  await expect(vitals).toBeVisible()

  // Scrolling past the header's own height collapses it (the threshold is
  // the expanded header's measured height, floored at 72px — see the view).
  await wheel(page, 1500)
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()
  await expect(toggle).toContainText('Details')

  // Deep in the feed nothing may paint between the collapsed header and the
  // transcript. While the PAGE scrolls that means the stuck header's own
  // background owns the scrollport's top edge (content bleeding through above
  // it was the -top/pt regression); once the transcript scrolls inside its own
  // region, it means the header ends above that region's top edge.
  // (Shallow scrolls can't assert either: before the header reaches its sticky
  // pin, the page chrome legitimately sits above it.)
  await wheel(page, 3000)
  await expect(digest).toBeVisible()
  const covered = await page.evaluate(() => {
    const header = document.querySelector('[data-testid="trace-sticky-header"]')
    if (!header) return null
    const region = document.querySelector('[data-trace-scroller]')
    if (region) {
      return header.getBoundingClientRect().bottom
        <= region.getBoundingClientRect().top + 1
    }
    const scroller = document.querySelector('.content-scroll')
    if (!scroller) return null
    const y = scroller.getBoundingClientRect().top + 2
    const el = document.elementFromPoint(window.innerWidth / 2, y)
    return el ? header.contains(el) : false
  })
  expect(covered, 'the collapsed sticky header overlaps the scrolled transcript').toBe(true)

  // Back at the top the header STAYS collapsed. Twelve events carry 2500px of
  // travel but only 12 x 26px of credited pull: reading back through the
  // transcript must not throw the details bar in the reader's face.
  await wheel(page, -2500, 12)
  await expect.poll(() => traceScrollTop(page),
    { message: 'scroller actually reached the top' }).toBeLessThan(24)
  await page.waitForTimeout(300)
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()

  // A sustained pull from that same resting position does reveal it — every
  // gate is open by now and only the accumulator is left to fill.
  await pull(page)
  await expect(vitals).toBeVisible()
  await expect(digest).toBeHidden()

  // Manual pin: collapse via the button, scroll mid-page — stays collapsed.
  await toggle.click()
  await expect(digest).toBeVisible()
  await wheel(page, 1500)
  await page.waitForTimeout(200)
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()

  // Returning to the top does NOT clear the pin. Once the reader has stated a
  // preference, a scroll position must stop guessing at it.
  await wheel(page, -2500, 12)
  await page.waitForTimeout(300)
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()

  // H is the keyboard twin of the button, and bypasses the accumulator.
  await page.keyboard.press('h')
  await expect(vitals).toBeVisible()
  await expect(digest).toBeHidden()
  await page.keyboard.press('H')
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()
})

test('the meter fills only on a sustained pull, and eases back when it stops', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const vitals = page.getByTestId('trace-vitals-strip')
  const digest = page.getByTestId('trace-header-digest')
  await expect(vitals).toBeVisible({ timeout: 10_000 })

  await setTraceScrollTop(page, 1500)
  await wheel(page, 1500)
  await expect(digest).toBeVisible()

  // Wait the 1.2s cooldown out first, so what the flick below proves is the
  // per-event cap and the dwell gate — not the cooldown standing in for them.
  await page.waitForTimeout(1400)

  // A hard flick that lands on the top edge: two enormous deltas, each clipped
  // to 26px, and both spent before the scroller has settled long enough to
  // open the dwell. Momentum must buy nothing at all.
  await wheel(page, -4000, 2)
  await expect.poll(() => traceScrollTop(page),
    { message: 'the flick actually reached the top' }).toBeLessThan(24)
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()
  expect(await meterFill(page), 'a flick must not move the meter').toBe(0)

  // Resting at the top the dwell opens, and a pull paints the meter. Sampled
  // WHILE the wheel runs: the accumulator decays as soon as the pull stops, so
  // a reading taken afterwards races the decay it is supposed to observe.
  await page.waitForTimeout(400)
  const pulling = wheel(page, -60, 14)
  const filled = await page.waitForFunction(() => {
    const bar = document.querySelector('[data-header-pull-bar]')
    const m = /scaleX\(([0-9.]+)\)/.exec(bar?.style.transform || '')
    return !!m && Number(m[1]) > 0.3
  }, null, { timeout: 5_000 }).then(() => true, () => false)
  await pulling
  expect(filled, 'a sustained pull must visibly fill the meter').toBe(true)

  // 14 x 26px = 364px, short of the 420px commit — so it eases back to empty
  // instead of revealing. A half-finished pull reads as reversible, not broken.
  await expect(digest).toBeVisible()
  await expect.poll(() => meterFill(page),
    { message: 'meter decays back to empty after the pull stops' }).toBe(0)
  await expect(digest).toBeVisible()
  await expect(vitals).toBeHidden()

  // A pull aimed at something OTHER than the transcript buys nothing: the
  // listeners are document-wide, so without a target check a wheel over the
  // sidebar (or over a span card that scrolls internally) would pop the header
  // open under a reader who was scrolling something else entirely.
  const side = await page.evaluate(() => {
    const r = document.querySelector('aside, nav')?.getBoundingClientRect()
    return r && r.width > 40 ? { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) } : null
  })
  expect(side, 'expected an app sidebar to aim the off-target wheel at').toBeTruthy()
  await page.mouse.move(side.x, side.y)
  for (let i = 0; i < 24; i++) await page.mouse.wheel(0, -60)
  await page.waitForTimeout(120)
  await expect(digest).toBeVisible()
  expect(await meterFill(page), 'an off-target wheel must not move the meter').toBe(0)

  // The complete gesture commits, and zeroes the meter behind it.
  await page.mouse.move(0, 0)
  await pull(page)
  await expect(vitals).toBeVisible()
  await expect(digest).toBeHidden()
  expect(await meterFill(page)).toBe(0)
})

test('a fold pinned inside the top band survives a layout-driven scroll nudge', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const traceId = await seedScrollableSession(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const digest = page.getByTestId('trace-header-digest')
  await expect(page.getByTestId('trace-vitals-strip')).toBeVisible({ timeout: 10_000 })

  // Fold a few px off the top, then let a layout-driven scroll (a live poll's
  // shift, written here directly) nudge the scroller onto the top edge. No
  // scroll of any kind may pop a hand-folded header back open under the reader.
  await setTraceScrollTop(page, 20)
  await page.waitForTimeout(300)
  await page.getByTestId('header-details-toggle').click()
  await expect(digest).toBeVisible()

  await setTraceScrollTop(page, 0)
  await page.waitForTimeout(400)
  await expect(digest).toBeVisible()

  // Leaving the band and coming back is not an unlock either — and while the
  // pin stands, even the full gesture is inert: `!headerPinned` is the first
  // gate of the ladder, so the machine stops fighting a reader who has already
  // chosen a header state by hand.
  await wheel(page, 1500)
  await expect(digest).toBeVisible()
  await wheel(page, -2500, 12)
  await page.waitForTimeout(300)
  await expect(digest).toBeVisible()
  await pull(page)
  await expect(digest).toBeVisible()

  // The explicit toggle is the way out, and always was.
  await page.getByTestId('header-details-toggle').click()
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
