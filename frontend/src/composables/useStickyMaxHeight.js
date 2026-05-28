import { ref, onMounted, onUnmounted, nextTick } from 'vue'

/**
 * useStickyMaxHeight — keep a `position: sticky` aside fit inside the
 * viewport in both its natural and stuck positions.
 *
 * Static `max-height: calc(100vh - header)` only works once the rail is
 * stuck to the top of the viewport. Before scroll, the rail still sits
 * at its natural document position and the calc puts its bottom below
 * the viewport. This composable measures the element's actual top
 * relative to the scroll container and returns a max-height string
 * that fits the available space below it, updating on scroll, resize,
 * and container resize.
 *
 * Extracted from SessionConversationView (PR 2.3c). Designed so the
 * sibling SessionTraceView (which also has a sticky inspector rail)
 * can reuse the same plumbing in a follow-up.
 *
 * @param {Ref<HTMLElement|null>} elRef - the aside element ref
 * @param {Object} [options]
 * @param {string} [options.scrollContainerSelector='.content-scroll']
 *   CSS selector used to find the scroll ancestor; falls back to
 *   `document.scrollingElement` / `window` when no ancestor matches.
 * @param {number} [options.bottomPadding=16] - reserved px below the rail.
 * @param {number} [options.minHeight=100] - returns '' (no inline cap)
 *   when available space falls below this threshold.
 *
 * @returns {{ maxH: Ref<string> }} — bind `:style="{maxHeight: maxH}"`.
 */
export function useStickyMaxHeight(elRef, options = {}) {
  const {
    scrollContainerSelector = '.content-scroll',
    bottomPadding = 16,
    minHeight = 100,
  } = options

  const maxH = ref('')

  function computeMaxH() {
    const el = elRef.value
    if (!el) return ''
    const scroller = el.closest(scrollContainerSelector) || document.scrollingElement
    if (!scroller) return ''
    const scrollerBottom = scroller === document.scrollingElement
      ? window.innerHeight
      : scroller.getBoundingClientRect().bottom
    const top = el.getBoundingClientRect().top
    const available = scrollerBottom - top - bottomPadding
    if (available < minHeight) return ''
    return `${Math.floor(available)}px`
  }

  let raf = 0
  function schedule() {
    if (raf) return
    raf = requestAnimationFrame(() => {
      raf = 0
      maxH.value = computeMaxH()
    })
  }

  let scrollTarget = null
  let resizeObs = null

  onMounted(async () => {
    await nextTick()
    const el = elRef.value
    scrollTarget = el?.closest(scrollContainerSelector) || window
    scrollTarget.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    if (el) {
      resizeObs = new ResizeObserver(schedule)
      // Observe the scroll container — its size changes when the page
      // header grows/shrinks, which moves the rail's available area.
      const scroller = el.closest(scrollContainerSelector)
      if (scroller) resizeObs.observe(scroller)
    }
    schedule()
  })

  onUnmounted(() => {
    if (raf) cancelAnimationFrame(raf)
    if (scrollTarget) scrollTarget.removeEventListener('scroll', schedule)
    window.removeEventListener('resize', schedule)
    if (resizeObs) { resizeObs.disconnect(); resizeObs = null }
  })

  return { maxH }
}
