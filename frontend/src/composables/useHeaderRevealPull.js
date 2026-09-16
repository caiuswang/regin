import { onScopeDispose } from 'vue'

// Revealing the collapsed trace header is a DELIBERATE gesture, not a scroll
// direction. Hiding stays cheap — one threshold, no ceremony — because losing
// the header costs the reader nothing. Getting it back costs a sustained pull
// past the top, because the old symmetric rule (any upward scroll re-expands)
// threw the details bar in the face of anyone reading back a few lines.
//
// The constants ARE the design. If reveal feels accidental, raise PULL_NEED
// and lower PER_EVENT_CAP, in that order, one at a time. If it feels sticky,
// shorten DWELL_MS before touching PULL_NEED — latency reads as
// unresponsiveness far faster than distance does. Never raise DECAY_PX_PER_MS
// to make the meter feel snappier; that reintroduces the snap it replaced.
const PULL_NEED = 420
// The load-bearing line. Momentum and overscroll arrive as a few huge deltas,
// a real pull as many small ones, so capping per event separates them without
// a velocity heuristic — a flick cannot buy more than a few events' worth.
const PER_EVENT_CAP = 26
const DRAIN_FACTOR = 2
const DRAIN_CAP = 40
const DWELL_MS = 250
const COOLDOWN_MS = 1200
const DECAY_PX_PER_MS = 1.4
const DECAY_IDLE_MS = 90
// A backgrounded tab resumes with one enormous frame delta; clamping keeps
// that from draining a pull the reader never released.
const FRAME_CLAMP_MS = 64
// Asymmetric on purpose: the gesture only counts at rest on the top edge, but
// it takes a clear departure (not a 1px anchor nudge) to throw the pull away.
const AT_TOP_PX = 2
const LEFT_TOP_PX = 4

// `canReveal` supplies the caller's own gates (collapsed, not user-pinned, at a
// breakpoint where the header folds at all); `onReveal` is the ONLY reactive
// write this module makes — everything up to the commit is painted imperatively.
export function useHeaderRevealPull({ getScroller, canReveal, onReveal }) {
  let pull = 0
  let pullChangedAt = 0
  // 0 means "not resting at the top"; the dwell is measured from the stamp.
  let atTopSince = 0
  let hidAt = -1e9
  let lastTouchY = null
  let raf = 0
  let lastFrameT = 0
  let lastPainted = -1
  let barEl = null
  let meterEl = null

  // Resolved by selector rather than a passed-down template ref: this module is
  // built during setup, before the view's template refs exist, and the meter is
  // v-show-gated on the fold. `isConnected` re-reads across a session switch,
  // whose `:key` swaps the whole header subtree for fresh nodes.
  function resolveEls() {
    if (!barEl?.isConnected) barEl = document.querySelector('[data-header-pull-bar]')
    if (!meterEl?.isConnected) meterEl = document.querySelector('[data-header-pull-meter]')
  }

  function paint(f) {
    if (f === lastPainted) return
    resolveEls()
    if (barEl) barEl.style.transform = `scaleX(${f.toFixed(4)})`
    if (meterEl) meterEl.style.opacity = f <= 0 ? '0' : Math.min(1, 0.3 + f * 1.6).toFixed(3)
    lastPainted = f
  }

  // One rAF owns decay AND paint. Putting progress in a ref instead would
  // re-render the whole trace every frame, and the page visibly shimmers.
  function frame(t) {
    const dt = Math.min(FRAME_CLAMP_MS, t - lastFrameT)
    lastFrameT = t
    const now = performance.now()
    // Commit BEFORE decay. `addPull` clamps at exactly PULL_NEED, so a frame
    // that arrives late — jank, a throttled tab, a slow reload tick — would
    // otherwise subtract up to a full idle window's worth and silently refund
    // a gesture the reader had already paid in full.
    if (pull >= PULL_NEED) {
      pull = 0
      paint(0)
      raf = 0
      onReveal()
      return
    }
    if (pull > 0 && now - pullChangedAt > DECAY_IDLE_MS) {
      pull = Math.max(0, pull - DECAY_PX_PER_MS * dt)
    }
    paint(pull / PULL_NEED)
    raf = pull > 0 ? requestAnimationFrame(frame) : 0
  }

  // The loop runs only while there is a pull to decay or draw — an idle trace
  // must not hold a frame callback open for the life of the session.
  function startLoop() {
    if (raf) return
    lastFrameT = performance.now()
    raf = requestAnimationFrame(frame)
  }

  function gateOpen() {
    if (!canReveal()) return false
    const now = performance.now()
    // Momentum from the very scroll that hid the header must not carry
    // straight through into a reveal.
    if (now - hidAt < COOLDOWN_MS) return false
    if ((getScroller()?.scrollTop ?? 0) > AT_TOP_PX) return false
    // A hard flick that lands on the top edge still carries its momentum; the
    // pull only starts counting once the scroller has settled there.
    return !!atTopSince && now - atTopSince >= DWELL_MS
  }

  // Positive delta is the reveal direction (content pulled downward).
  function addPull(delta) {
    if (delta > 0) {
      if (!gateOpen()) return
      pull = Math.min(PULL_NEED, pull + Math.min(PER_EVENT_CAP, delta))
    } else if (delta < 0) {
      if (pull <= 0) return
      // Changing your mind is instant — draining is uncapped by the gates and
      // twice as fast as filling.
      pull = Math.max(0, pull - Math.min(DRAIN_CAP, -delta * DRAIN_FACTOR))
    } else {
      return
    }
    pullChangedAt = performance.now()
    startLoop()
  }

  function clearPull() {
    if (pull > 0) {
      pull = 0
      paint(0)
    }
  }

  // The listeners are document-wide (a capture-phase wheel is the only way to
  // see a gesture the body scroller never receives), so each event has to earn
  // its way in. Two things disqualify one: a target outside the trace scroller
  // altogether — the sidebar, a popover, the app shell — and a nested scroller
  // between that target and the body which can still take the scroll itself.
  // Several span cards scroll internally with overscroll containment, so their
  // wheel events never reach the body; crediting them would pop the header
  // open under a reader who was scrolling something else entirely, which is
  // the reported bug wearing a different hat.
  function ownsGesture(target) {
    const scroller = getScroller()
    if (!scroller || !(target instanceof Node) || !scroller.contains(target)) return false
    let el = target instanceof Element ? target : target.parentElement
    while (el && el !== scroller) {
      if (el.scrollTop > 0 && el.scrollHeight > el.clientHeight) return false
      el = el.parentElement
    }
    return true
  }

  function onWheel(e) {
    if (ownsGesture(e.target)) addPull(-e.deltaY)
  }
  function onTouchStart(e) { lastTouchY = e.touches?.[0]?.clientY ?? null }
  function onTouchEnd() { lastTouchY = null }
  function onTouchMove(e) {
    const y = e.touches?.[0]?.clientY
    if (y == null) return
    if (lastTouchY != null && ownsGesture(e.target)) addPull(y - lastTouchY)
    lastTouchY = y
  }

  // Scroll only gates — it never adds pull. The caller feeds it from its own
  // filtered scroll handler so this module needs no second listener.
  function noteScroll(top) {
    if (top > LEFT_TOP_PX) {
      atTopSince = 0
      clearPull()
      return
    }
    if (top <= AT_TOP_PX && !atTopSince) atTopSince = performance.now()
  }

  function noteHidden() {
    hidAt = performance.now()
    clearPull()
  }

  function reset() {
    atTopSince = 0
    clearPull()
  }

  const opts = { passive: true, capture: true }
  document.addEventListener('wheel', onWheel, opts)
  document.addEventListener('touchstart', onTouchStart, opts)
  document.addEventListener('touchmove', onTouchMove, opts)
  document.addEventListener('touchend', onTouchEnd, opts)
  document.addEventListener('touchcancel', onTouchEnd, opts)

  onScopeDispose(() => {
    document.removeEventListener('wheel', onWheel, opts)
    document.removeEventListener('touchstart', onTouchStart, opts)
    document.removeEventListener('touchmove', onTouchMove, opts)
    document.removeEventListener('touchend', onTouchEnd, opts)
    document.removeEventListener('touchcancel', onTouchEnd, opts)
    if (raf) cancelAnimationFrame(raf)
    raf = 0
  })

  return { noteScroll, noteHidden, reset, PULL_NEED }
}
