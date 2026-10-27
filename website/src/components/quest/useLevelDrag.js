import { MAP } from '../../content/quest.js'

const DRAG_THRESHOLD_PX = 4

function clampTile(x, y) {
  return {
    x: Math.min(MAP.cols - 3, Math.max(2, x)),
    y: Math.min(MAP.rows - 2, Math.max(3, y)),
  }
}

export function useLevelDrag(stage, positions, onSelect) {
  let active = null

  function tileAt(event) {
    const box = stage.value.getBoundingClientRect()
    return {
      x: Math.floor(((event.clientX - box.left) / box.width) * MAP.cols),
      y: Math.floor(((event.clientY - box.top) / box.height) * MAP.rows),
    }
  }

  function onPointerDown(id, event) {
    if (event.button !== 0) return
    active = { id, x0: event.clientX, y0: event.clientY, moved: false }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function onPointerMove(event) {
    if (!active) return
    if (!active.moved && Math.hypot(event.clientX - active.x0, event.clientY - active.y0) < DRAG_THRESHOLD_PX) return
    active.moved = true
    const tile = tileAt(event)
    Object.assign(positions[active.id], clampTile(tile.x, tile.y + 1))
  }

  function onPointerUp() {
    if (active && !active.moved) onSelect(active.id)
    active = null
  }

  function onKey(id, event) {
    const step = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key]
    if (step) {
      event.preventDefault()
      const p = positions[id]
      Object.assign(p, clampTile(p.x + step[0], p.y + step[1]))
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelect(id)
    }
  }

  return { onPointerDown, onPointerMove, onPointerUp, onKey }
}
