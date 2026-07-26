import { ref, watch, nextTick } from 'vue'

// Tween an element's height across a v-if/v-show fold that would otherwise
// swap layouts in one frame. The old height is read before Vue patches the
// DOM (flush: 'pre'), the new one after, and a WAAPI animation bridges the
// two with the content clipped.
//
// A layout tween may only run when the scroll is SETTLED. Every tween frame
// is a layout change, and Chromium kills an in-flight native smooth scroll
// within a frame of one — a wheel flick thrown mid-glide dies and strands
// the reader mid-feed. There is no event-level escape: cancelling the tween
// from a wheel listener is itself a layout jump in that wheel's frame and
// eats the scroll just the same (compositor-only animations are exempt, but
// they cannot move in-flow content). So transitions that can fire while the
// user is still scrolling must not glide: `glideWhen` picks the states that
// are safe by construction, and `glideNext()` arms a one-shot glide for a
// transition the caller knows is scroll-settled (a click, a keypress).
//
// Exposes `animating` so callers can ignore mid-tween measurements (a
// threshold fed by a half-grown height re-arms the very oscillation a
// measured threshold exists to prevent), and `snap()` for transitions that
// must not glide at all, e.g. breakpoint crossings.
export function useFoldTransition(elRef, stateRef, { duration = 220, glideWhen = null } = {}) {
  const animating = ref(false)
  let anim = null
  let skip = false
  let glideArmed = false

  function cancelRunning() {
    if (!anim) return
    // Cancel events fire async: a live handler would clear the replacement
    // animation's clip after it starts.
    anim.onfinish = anim.oncancel = null
    anim.cancel()
    anim = null
    animating.value = false
    if (elRef.value) elRef.value.style.overflow = ''
  }

  function snap() {
    cancelRunning()
    skip = true
    nextTick(() => { skip = false })
  }

  // One-shot, consumed by the next state change in the watch below — NOT
  // cleared via nextTick: with no flush pending, that clear would drain on
  // the microtask ahead of the watcher flush and disarm before it's read.
  function glideNext() {
    glideArmed = true
  }

  watch(stateRef, (state) => {
    const armed = glideArmed
    glideArmed = false
    const el = elRef.value
    if (!el || skip) return
    if (glideWhen && !glideWhen(state) && !armed) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    // Mid-animation retoggles read the tweened height, so a reversal
    // continues from where the fold visually is instead of jumping.
    const from = el.getBoundingClientRect().height
    nextTick(() => {
      const target = elRef.value
      if (!target || skip) return
      cancelRunning()
      const to = target.getBoundingClientRect().height
      if (Math.abs(from - to) < 1) return
      target.style.overflow = 'hidden'
      animating.value = true
      anim = target.animate(
        [{ height: `${from}px` }, { height: `${to}px` }],
        { duration, easing: 'cubic-bezier(0.4, 0, 0.2, 1)' },
      )
      const settle = () => {
        target.style.overflow = ''
        anim = null
        animating.value = false
      }
      anim.onfinish = settle
      anim.oncancel = settle
    })
  }, { flush: 'pre' })

  return { animating, snap, glideNext }
}
