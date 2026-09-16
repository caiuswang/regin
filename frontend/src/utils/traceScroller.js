// The trace view's active scroll region.
//
// Which element scrolls depends on the layout: above the `meta.fixedViewport`
// threshold the app shell's `.content-scroll` is frozen and the region that
// moves lives inside the view — the conversation feed's own scroll box, or the
// card the timeline/terminal/messages tabs render into. Whichever one is
// mounted marks itself `data-trace-scroller`, so nothing has to hardcode a
// container that may not be the one the reader is scrolling.
export function getTraceScroller() {
  return document.querySelector('[data-trace-scroller]')
    || document.querySelector('.content-scroll')
    || document.scrollingElement
    || document.documentElement
}
