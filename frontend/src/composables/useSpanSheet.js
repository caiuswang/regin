import { ref, watch } from 'vue'

// Mobile span-detail sheet intent. Selecting a span does NOT open the sheet:
// below lg a tap on a row means "expand this one", and a modal over the feed
// on every tap cost a dismissal per read. The sheet is left to the `?span=`
// deep link, which names one span and asks for nothing else; the ⤢ reader is
// the tap-reachable route to a span's full content. It closes when the
// viewport crosses up into the desktop rail. Closing keeps the selection
// (desktop parity).
export function useSpanSheet(selectedSpan, isLgUp, deepLinkSpanId) {
  const sheetOpen = ref(false)

  function selectSpan(span) {
    selectedSpan.value = span
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
