// Scroll the trace row marked with `data-span-id` into the vertical center of
// the `.content-scroll` viewport. Shared by the deep-link resolver, the
// overview-strip click handler, and the turns cross-highlight.
//
// Lazy-loaded child rows take a few cycles to mount + lay out in PrimeVue, so
// we poll for the row (up to 20 × 50ms) instead of guessing a tick count.
// Stateless: no closure over component state.
export function scrollSpanRowIntoView(spanId, attempt = 0) {
  // Both views mark rows with data-span-id: the timeline tree on the
  // inner cell <div>, the terminal log directly on the <tr>.
  const el = document.querySelector(`[data-span-id="${spanId}"]`)
  const row = el?.closest('tr') || el
  const scroller = document.querySelector('.content-scroll')
  if (!row || !scroller) {
    if (attempt < 20) setTimeout(() => scrollSpanRowIntoView(spanId, attempt + 1), 50)
    return
  }
  // PrimeVue's table wrapper has overflow: auto on both axes, so
  // scrollIntoView stops there and never reaches `.content-scroll`.
  // Compute the offset ourselves and jump instantly — `behavior: 'smooth'`
  // gets cancelled by PrimeVue's continuing tree re-renders and leaves
  // the scroll stuck near zero.
  const rowRect = row.getBoundingClientRect()
  const scrollerRect = scroller.getBoundingClientRect()
  const offset = scroller.scrollTop + (rowRect.top - scrollerRect.top)
  const top = offset - scroller.clientHeight / 2 + row.offsetHeight / 2
  scroller.scrollTo({ top: Math.max(0, top), behavior: 'auto' })
}
