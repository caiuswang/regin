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
  const mqMd = canQuery ? window.matchMedia('(min-width: 768px)') : null
  const mqLg = canQuery ? window.matchMedia('(min-width: 1024px)') : null
  const mqXl = canQuery ? window.matchMedia('(min-width: 1280px)') : null
  const mq2xl = canQuery ? window.matchMedia('(min-width: 1536px)') : null
  const isMdUp = ref(mqMd ? mqMd.matches : false)
  const isLgUp = ref(mqLg ? mqLg.matches : false)
  const isXl = ref(mqXl ? mqXl.matches : false)
  const is2xl = ref(mq2xl ? mq2xl.matches : false)
  const sync = () => {
    isMdUp.value = mqMd ? mqMd.matches : false
    isLgUp.value = mqLg ? mqLg.matches : false
    isXl.value = mqXl ? mqXl.matches : false
    is2xl.value = mq2xl ? mq2xl.matches : false
  }
  const queries = [mqMd, mqLg, mqXl, mq2xl].filter(Boolean)
  onMounted(() => {
    if (!canQuery) return
    sync()
    for (const mq of queries) mq.addEventListener('change', sync)
  })
  onUnmounted(() => {
    for (const mq of queries) mq.removeEventListener('change', sync)
  })

  return { isMdUp, isLgUp, isXl, is2xl }
}
