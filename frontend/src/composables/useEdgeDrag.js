import { ref } from 'vue'

const DRAG_THRESHOLD = 4

/**
 * Drag-to-connect for the lane graph's node handles: press a handle, drag onto
 * another node, release. A press that never travels past the threshold stays a
 * click, so the same handle still opens the picker.
 *
 * `stage` is the unscaled, zoom-transformed layer; pointer positions are
 * returned in its coordinates so the preview line lives in the edge SVG.
 */
export function useEdgeDrag({ stage, zoom, onClick, onDrop }) {
  const drag = ref(null)
  let justDragged = false

  function stagePoint(ev) {
    const r = stage.value.getBoundingClientRect()
    return { x: (ev.clientX - r.left) / zoom.value, y: (ev.clientY - r.top) / zoom.value }
  }

  function nodeUnder(ev) {
    const el = document.elementFromPoint(ev.clientX, ev.clientY)?.closest('[data-node-id]')
    const id = el ? Number(el.dataset.nodeId) : null
    return id === drag.value?.node.id ? null : id
  }

  function move(ev) {
    const d = drag.value
    if (!d) return
    if (!d.active && Math.hypot(ev.clientX - d.startX, ev.clientY - d.startY) < DRAG_THRESHOLD) return
    drag.value = { ...d, active: true, ...stagePoint(ev), target: nodeUnder(ev) }
  }

  function detach() {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    window.removeEventListener('pointercancel', cancel)
  }

  function cancel() {
    detach()
    drag.value = null
  }

  function up(ev) {
    const d = drag.value
    detach()
    drag.value = null
    if (!d?.active) return
    justDragged = true
    const target = nodeUnder(ev) ?? d.target
    if (target != null && target !== d.node.id) onDrop(d.node, d.side, target)
  }

  function start(node, side, ev) {
    if (ev.button !== 0 || drag.value) return
    ev.preventDefault()
    justDragged = false
    drag.value = { node, side, startX: ev.clientX, startY: ev.clientY, active: false, target: null, x: 0, y: 0 }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', cancel)
  }

  // A drag released over its own handle still fires that handle's click;
  // keyboard activation (detail 0) is never a drag and always opens.
  function click(node, side, ev) {
    const swallow = justDragged && ev.detail !== 0
    justDragged = false
    if (!swallow) onClick(node, side)
  }

  return { drag, start, click, cancel }
}
