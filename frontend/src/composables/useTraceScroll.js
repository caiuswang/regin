import { onMounted, onUnmounted } from 'vue'

// Scroll/wheel/touch-driven auto-reload for the trace view. Extracted from
// SessionTraceView so the view's setup stays focused on data, not DOM
// mechanics.
//
// Two affordances, both edge-triggered:
//   - scrolling to within a threshold of the bottom/top of ANY scroll
//     container on the page fires reload()/loadOlder() (`onAnyScroll`);
//   - while already parked at the absolute bottom/top (where no scroll event
//     can fire) a downward/upward wheel or touch gesture acts as
//     pull-to-refresh / pull-older (`onAnyWheel` + touch shim).
//
// Listeners are attached at the document level in capture phase so they catch
// scroll from nested containers (`.content-scroll`, panels, the window) —
// scroll events don't bubble, so a bubbling document listener would miss them.
//
// Deps are passed in so the composable owns no data state: `reloading`,
// `loading`, `loadingOlder`, `hasMoreOlder`, `liveSyncActive` are refs read via
// `.value`; `reload` and `loadOlder` are the view's loader callbacks.
//
// `liveSyncActive` gates only the bottom-edge pull-to-refresh (both the
// scroll-near-bottom auto-reload and the wheel-down-at-bottom gesture). It goes
// false once the session's live-sync self-terminates — an ended, converged
// session — so scrolling to the end of a closed session no longer re-fires
// reloadLiveTail() and its backend transcript rescan. `loadOlder` (the top
// edge) is paginating immutable history, never live-sync, so it ignores this.
export function useTraceScroll({ reloading, loading, loadingOlder, hasMoreOlder, liveSyncActive, reload, loadOlder }) {
  // Treat an absent ref as always-live, so a caller that doesn't pass it keeps
  // the original behaviour.
  const syncActive = () => (liveSyncActive ? liveSyncActive.value : true)
  // Edge-trigger latches: keep parking at an edge with no new spans from
  // re-firing the loader every scroll tick.
  let bottomLatch = false
  let topLatch = false
  let lastWheelReloadAt = 0
  let lastTouchY = null

  // Manual wheel/trackpad scrolling often stops short of the absolute edge;
  // a generous threshold keeps the affordance responsive.
  const BOTTOM_THRESHOLD_PX = 240
  const TOP_THRESHOLD_PX = 240
  // Min ms between wheel-triggered reloads, so sustained wheel motion while
  // parked at an edge doesn't fire the loader back-to-back.
  const WHEEL_RELOAD_COOLDOWN_MS = 1500

  // Bottom edge: fire the pull-to-refresh reload once per approach, gated on
  // live-sync still being active (a closed, converged session does not reload).
  function handleBottomEdge(el) {
    const distBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    if (distBottom > BOTTOM_THRESHOLD_PX) {
      bottomLatch = false
      return
    }
    if (!bottomLatch && !reloading.value && !loading.value && syncActive()) {
      bottomLatch = true
      reload()
    }
  }

  // Top edge: pull the next page of older (immutable) history. Never gated on
  // live-sync — paginating history is valid on a closed session.
  function handleTopEdge(el) {
    if (el.scrollTop > TOP_THRESHOLD_PX || !hasMoreOlder.value) {
      topLatch = false
      return
    }
    if (!topLatch && !loadingOlder.value && !loading.value) {
      topLatch = true
      loadOlder()
    }
  }

  function onAnyScroll(e) {
    const t = e.target
    const el = (t === document || t === document.documentElement)
      ? (document.scrollingElement || document.documentElement)
      : t
    if (!el || typeof el.scrollHeight !== 'number') return
    handleBottomEdge(el)
    handleTopEdge(el)
  }

  // Nearest scrollable ancestor of `el` (the container actually overflowing),
  // falling back to the document scroller.
  function _findScrollerNearTarget(el) {
    while (el && el !== document.body && el !== document.documentElement) {
      if (el.scrollHeight && el.clientHeight && el.scrollHeight > el.clientHeight) {
        const style = getComputedStyle(el)
        if (/(auto|scroll)/.test(style.overflowY)) return el
      }
      el = el.parentElement
    }
    return document.scrollingElement || document.documentElement
  }

  function onAnyWheel(e) {
    const scroller = _findScrollerNearTarget(e.target)
    if (!scroller) return
    const now = Date.now()
    if (e.deltaY > 0) {
      // Wheel-down at the absolute bottom → reload (pull-to-refresh).
      const dist = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight
      if (dist > 4) return
      if (reloading.value || loading.value) return
      // Live-sync retired (closed, converged session): a deliberate pull-down
      // would still fire a backend rescan on a dead session — gate it too.
      if (!syncActive()) return
      if (now - lastWheelReloadAt < WHEEL_RELOAD_COOLDOWN_MS) return
      lastWheelReloadAt = now
      reload()
    } else if (e.deltaY < 0) {
      // Wheel-up at the absolute top → load older.
      if (scroller.scrollTop > 4) return
      if (loadingOlder.value || loading.value) return
      if (!hasMoreOlder.value) return
      if (now - lastWheelReloadAt < WHEEL_RELOAD_COOLDOWN_MS) return
      lastWheelReloadAt = now
      loadOlder()
    }
  }

  function onTouchStart(e) {
    if (e.touches && e.touches.length) lastTouchY = e.touches[0].clientY
  }

  function onTouchMove(e) {
    if (!e.touches || !e.touches.length || lastTouchY == null) return
    const y = e.touches[0].clientY
    const deltaY = lastTouchY - y  // positive = swiping content up = scrolling down
    lastTouchY = y
    if (deltaY === 0) return
    onAnyWheel({ deltaY, target: e.target })
  }

  onMounted(() => {
    // Capture phase so we see events from descendants like `.content-scroll`;
    // scroll events don't bubble, so a bubbling listener on document never
    // fires for nested scroll containers.
    document.addEventListener('scroll', onAnyScroll, { capture: true, passive: true })
    document.addEventListener('wheel', onAnyWheel, { capture: true, passive: true })
    document.addEventListener('touchstart', onTouchStart, { capture: true, passive: true })
    document.addEventListener('touchmove', onTouchMove, { capture: true, passive: true })
  })

  onUnmounted(() => {
    document.removeEventListener('scroll', onAnyScroll, { capture: true })
    document.removeEventListener('wheel', onAnyWheel, { capture: true })
    document.removeEventListener('touchstart', onTouchStart, { capture: true })
    document.removeEventListener('touchmove', onTouchMove, { capture: true })
  })
}
