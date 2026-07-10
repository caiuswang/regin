import { ref } from 'vue'

// Generic resizable-panel width: dragged via a handle on the panel's edge (or
// ←/→ when the handle is focused) and persisted in localStorage under `key`.
// Clamped so the panel can't collapse or swallow its neighbor. Generalized
// from useConversationRail so other split layouts can reuse it.
export function useResizablePanel(key, { min = 176, max = 560, def = 288 } = {}) {
  const clamp = (w) => Math.min(max, Math.max(min, w))
  const width = ref((() => {
    const v = parseInt(localStorage.getItem(key), 10)
    return Number.isFinite(v) ? clamp(v) : def
  })())

  let startX = 0
  let startW = 0

  function onMove(e) {
    width.value = clamp(startW + (e.clientX - startX))
  }
  function onEnd() {
    document.removeEventListener('pointermove', onMove)
    document.removeEventListener('pointerup', onEnd)
    document.removeEventListener('pointercancel', onEnd)
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    localStorage.setItem(key, String(Math.round(width.value)))
  }
  function onResizeStart(e) {
    startX = e.clientX
    startW = width.value
    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onEnd)
    document.addEventListener('pointercancel', onEnd)
    document.body.style.userSelect = 'none'      // suppress text selection while dragging
    document.body.style.cursor = 'col-resize'
    e.preventDefault()
  }
  function onResizeKey(e) {
    const step = e.shiftKey ? 32 : 8
    if (e.key === 'ArrowLeft') width.value = clamp(width.value - step)
    else if (e.key === 'ArrowRight') width.value = clamp(width.value + step)
    else return
    e.preventDefault()
    localStorage.setItem(key, String(Math.round(width.value)))
  }

  return { width, onResizeStart, onResizeKey }
}
