import { ref, computed, onBeforeUnmount } from 'vue'

const STEP_MS = 1800

// Reveals a flow's steps one at a time; under reduced motion, all at once.
export function useFlowSteps(flows) {
  const flowIndex = ref(-1)
  const shown = ref(0)
  let timer = null

  const flow = computed(() => flows[flowIndex.value] || null)
  const steps = computed(() => (flow.value ? flow.value.steps.slice(0, shown.value) : []))

  // part id → the 1-based step numbers it has played so far
  const badges = computed(() => {
    const out = {}
    steps.value.forEach(([id], i) => { (out[id] ||= []).push(i + 1) })
    return out
  })
  const current = computed(() => steps.value.at(-1)?.[0] ?? null)

  function stop() {
    clearInterval(timer)
    timer = null
  }

  function pick(i) {
    stop()
    flowIndex.value = i
    const total = flows[i].steps.length
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      shown.value = total
      return
    }
    shown.value = 1
    timer = setInterval(() => {
      shown.value += 1
      if (shown.value >= total) stop()
    }, STEP_MS)
  }

  function clear() {
    stop()
    flowIndex.value = -1
    shown.value = 0
  }

  onBeforeUnmount(stop)
  return { flowIndex, flow, steps, badges, current, pick, clear }
}
