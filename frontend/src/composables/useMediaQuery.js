import { ref, onMounted, onUnmounted } from 'vue'

// One reactive `matchMedia` flag, for a breakpoint that is NOT on the
// Tailwind scale useBreakpoint covers. Initialized synchronously so the
// first frame renders at the real width rather than flashing the wrong
// branch, and torn down on unmount.
export function useMediaQuery(query) {
  const canQuery = typeof window !== 'undefined' && !!window.matchMedia
  const mq = canQuery ? window.matchMedia(query) : null
  const matches = ref(mq ? mq.matches : false)
  const sync = () => { matches.value = mq ? mq.matches : false }
  onMounted(() => { if (mq) { sync(); mq.addEventListener('change', sync) } })
  onUnmounted(() => { if (mq) mq.removeEventListener('change', sync) })
  return matches
}
