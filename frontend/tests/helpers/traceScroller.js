/**
 * The trace view's active scroll region, resolved the way the app resolves it
 * (`src/utils/traceScroller.js`): above the bounded threshold the transcript
 * scrolls inside the view and the shell's `.content-scroll` is frozen, below
 * it the shell is still the scroller. A spec that hardcodes either one asserts
 * against a box that stands still at half the viewport widths it runs at.
 */
export const traceScrollTop = (page) =>
  page.evaluate(() => Math.round((
    document.querySelector('[data-trace-scroller]')
    || document.querySelector('.content-scroll')
  ).scrollTop))

/** `top` is a pixel offset, or 'bottom' for the end of the region. */
export const setTraceScrollTop = (page, top) =>
  page.evaluate((t) => {
    const el = document.querySelector('[data-trace-scroller]')
      || document.querySelector('.content-scroll')
    el.scrollTop = t === 'bottom' ? el.scrollHeight : t
  }, top)

/**
 * Real wheel input over the trace scroller's own right gutter. The gutter is
 * the one strip of the region no span card reaches — several cards scroll
 * internally with overscroll containment and would swallow the wheel — and the
 * header state machine only reacts to scrolls backed by real input, so a
 * scrollTop write is not a substitute.
 */
export async function wheelOverTrace(page, deltaY, times = 1) {
  const at = await page.evaluate(() => {
    const el = document.querySelector('[data-trace-scroller]')
      || document.querySelector('.content-scroll')
    const r = el.getBoundingClientRect()
    return { x: Math.round(r.right - 6), y: Math.round((r.top + r.bottom) / 2) }
  })
  await page.mouse.move(at.x, at.y)
  for (let i = 0; i < times; i++) {
    await page.mouse.wheel(0, deltaY)
    await page.waitForTimeout(40)
  }
}
