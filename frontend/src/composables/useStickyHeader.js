import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'

// Track the rendered height of a sticky page header so dependent sticky
// elements (tbody thead, side panels) can offset themselves correctly.
// Returns a template ref `stickyHeaderEl` to bind to the sticky <div>,
// plus a reactive `stickyHeaderHeight` (px). `gateRef` is an optional
// ref (e.g. `loading`) whose flip to falsy triggers a re-attach after
// the v-else branch renders.
export function useStickyHeader(gateRef = null) {
  const stickyHeaderEl = ref(null)
  const stickyHeaderHeight = ref(0)
  let ro = null

  function attach() {
    if (ro || !stickyHeaderEl.value) return
    const measure = () => {
      if (stickyHeaderEl.value) {
        stickyHeaderHeight.value = stickyHeaderEl.value.getBoundingClientRect().height
      }
    }
    measure()
    ro = new ResizeObserver(measure)
    ro.observe(stickyHeaderEl.value)
  }

  onMounted(async () => {
    await nextTick()
    attach()
  })

  onUnmounted(() => {
    if (ro) { ro.disconnect(); ro = null }
  })

  if (gateRef) {
    watch(gateRef, async (v) => {
      if (!v) {
        await nextTick()
        attach()
      }
    })
  }

  return { stickyHeaderEl, stickyHeaderHeight }
}
