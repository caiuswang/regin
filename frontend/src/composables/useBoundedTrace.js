import { ref, onMounted, onUnmounted } from 'vue'

// Is the trace view laid out as bounded, internally-scrolling regions?
//
// Mirrors AppLayout's `.content-scroll-fixed-lg` freeze (the route's
// `meta.fixedViewport: 'lg'`) — the shell stops scrolling at exactly this
// width, so the view has to take its own scrolling over at the same one or the
// page ends up with two scrollers, or none. Every bounded-layout rule keys off
// the class this drives rather than re-spelling the threshold in CSS.
// Initialized synchronously at setup: a mounted-only init leaves the first
// frame claiming the shell still scrolls, which is when the feed's pin/follow
// machinery latches onto its scroller.
const BOUNDED_MQ = '(min-width: 1200px)'

export function useBoundedTrace() {
  const mq = (typeof window !== 'undefined' && window.matchMedia)
    ? window.matchMedia(BOUNDED_MQ) : null
  const bounded = ref(mq ? mq.matches : false)
  const sync = () => { bounded.value = mq ? mq.matches : false }
  onMounted(() => {
    if (!mq) return
    sync()
    mq.addEventListener('change', sync)
  })
  onUnmounted(() => { if (mq) mq.removeEventListener('change', sync) })
  return bounded
}
