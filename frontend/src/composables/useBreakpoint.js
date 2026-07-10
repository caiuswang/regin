import { ref, onMounted, onUnmounted } from 'vue'

// Reactive Tailwind-scale breakpoint flags for layout that must branch in JS
// rather than CSS — the trace view's agent scope renders as a companion pane
// at ≥xl and a full-feed takeover below it, which is a structural (component)
// switch, not a style toggle. Mirrors Tailwind's `xl` (1280px) and `2xl`
// (1536px) min-width breakpoints; matchMedia so it tracks live resizes and
// the E2E viewport overrides. Initialized synchronously at setup — a
// mounted-only init left the refs false for the first frame, flashing the
// full-width takeover on ≥xl `?agent=` deep links.
export function useBreakpoint() {
  const canQuery = typeof window !== 'undefined' && !!window.matchMedia
  const mqXl = canQuery ? window.matchMedia('(min-width: 1280px)') : null
  const mq2xl = canQuery ? window.matchMedia('(min-width: 1536px)') : null
  const isXl = ref(mqXl ? mqXl.matches : false)
  const is2xl = ref(mq2xl ? mq2xl.matches : false)
  const sync = () => {
    isXl.value = mqXl ? mqXl.matches : false
    is2xl.value = mq2xl ? mq2xl.matches : false
  }
  onMounted(() => {
    if (!canQuery) return
    sync()
    mqXl.addEventListener('change', sync)
    mq2xl.addEventListener('change', sync)
  })
  onUnmounted(() => {
    if (mqXl) mqXl.removeEventListener('change', sync)
    if (mq2xl) mq2xl.removeEventListener('change', sync)
  })

  return { isXl, is2xl }
}
