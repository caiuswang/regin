import { computed, ref } from 'vue'

// Drag-down-to-close for a bottom sheet. Extracted from BlockerBanner when the
// sheet became the blocker pop-out, so the gesture — and the two race fixes
// baked into it — moved with the sheet instead of being rewritten beside it.

const DISMISS_AFTER_PX = 90
const TAP_SLOP_PX = 6

export function useSheetDrag(onDismiss) {
  const dragY = ref(0)
  const dragging = ref(false)
  let dragFrom = 0
  let moved = 0
  let swallowClick = false

  const sheetStyle = computed(() => ({
    transform: dragY.value ? `translateY(${dragY.value}px)` : undefined,
    transition: dragging.value ? 'none' : 'transform 0.26s cubic-bezier(0.2, 0.9, 0.3, 1)',
  }))

  function settle() {
    dragY.value = 0
    dragging.value = false
    // A swipe that closes unmounts the sheet before the click task runs, so
    // `handleClick` never gets to clear the flag — and a stale `true` would eat
    // the next real activation (an Enter on the handle after it re-opens).
    swallowClick = false
    onDismiss()
  }

  function dragStart(event) {
    dragFrom = event.clientY
    moved = 0
    dragging.value = true
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }

  function dragMove(event) {
    if (!dragging.value) return
    const delta = event.clientY - dragFrom
    moved = Math.max(moved, Math.abs(delta))
    dragY.value = Math.max(0, delta)
  }

  function dragEnd() {
    if (!dragging.value) return
    // A drag that snapped back said "keep it" — but the browser still fires a
    // click on pointerup, which would close the sheet the user just kept.
    swallowClick = moved > TAP_SLOP_PX
    if (dragY.value > DISMISS_AFTER_PX) settle()
    else {
      dragging.value = false
      dragY.value = 0
    }
  }

  // A cancelled gesture fires no click, so the flag must not survive it and eat
  // the next real tap (or an Enter on the focused handle).
  function dragCancel() {
    dragEnd()
    swallowClick = false
  }

  function handleClick() {
    if (swallowClick) {
      swallowClick = false
      return
    }
    settle()
  }

  return {
    dragY, dragging, sheetStyle,
    dragStart, dragMove, dragEnd, dragCancel, handleClick,
  }
}
