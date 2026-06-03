import { ref } from 'vue'

// Resizable left-rail width: dragged via the handle on the rail's right edge
// (or ←/→ when focused) and persisted in localStorage. Clamped so the rail
// can't collapse or swallow the chat column. Default 224px = the previous
// fixed `w-56`.
const RAIL_MIN = 176
const RAIL_MAX = 560
const RAIL_KEY = 'regin_trace_rail_width'

function clampRail(w) { return Math.min(RAIL_MAX, Math.max(RAIL_MIN, w)) }

export function useConversationRail() {
  const railWidth = ref((() => {
    const v = parseInt(localStorage.getItem(RAIL_KEY), 10)
    return Number.isFinite(v) ? clampRail(v) : 224
  })())

  let startX = 0
  let startW = 0

  function onMove(e) {
    railWidth.value = clampRail(startW + (e.clientX - startX))
  }
  function onEnd() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onEnd)
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    localStorage.setItem(RAIL_KEY, String(Math.round(railWidth.value)))
  }
  function onRailResizeStart(e) {
    startX = e.clientX
    startW = railWidth.value
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onEnd)
    document.body.style.userSelect = 'none'      // suppress text selection while dragging
    document.body.style.cursor = 'col-resize'
    e.preventDefault()
  }
  function onRailResizeKey(e) {
    const step = e.shiftKey ? 32 : 8
    if (e.key === 'ArrowLeft') railWidth.value = clampRail(railWidth.value - step)
    else if (e.key === 'ArrowRight') railWidth.value = clampRail(railWidth.value + step)
    else return
    e.preventDefault()
    localStorage.setItem(RAIL_KEY, String(Math.round(railWidth.value)))
  }

  return { railWidth, onRailResizeStart, onRailResizeKey }
}
