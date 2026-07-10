import { ref, watch } from 'vue'

// Mobile span-detail sheet intent. The sheet opens on explicit selection or
// a `?span=` deep link — never on the chat-style default selection seeded by
// loadSession — and closes when the viewport crosses up into the desktop
// rail. Closing keeps the selection (desktop parity).
export function useSpanSheet(selectedSpan, isLgUp, deepLinkSpanId) {
  const sheetOpen = ref(false)

  function selectSpan(span) {
    selectedSpan.value = span
    sheetOpen.value = !!span && !isLgUp.value
  }

  watch(isLgUp, (up) => {
    if (up) sheetOpen.value = false
  })

  if (deepLinkSpanId) {
    const stopDeepLinkSheet = watch(selectedSpan, (s) => {
      if (s?.span_id !== deepLinkSpanId) return
      if (!isLgUp.value) sheetOpen.value = true
      stopDeepLinkSheet()
    })
  }

  return { sheetOpen, selectSpan }
}
