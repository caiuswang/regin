import { inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { caretPoint } from '../utils/caretPoint.js'
import { useConfirm } from './useConfirm'

// `MarkdownEditor`'s layout: `inline` in its host's card, or `focus`, where the
// page drops everything but the editor. The page owns what it hides, so the
// editor only flips the `editorFocus` ref the page provides; a page that
// provides none still gets the centred column.
//
// Switching re-flows everything above and around the editor, so the block the
// writer is in is pinned: measured against the scroller before the switch,
// scrolled back to the same offset after it (or into view, if it was off
// screen). The block is the one holding the caret while the editor has focus,
// and otherwise the one at the top of the visible part of the editor.
const SCROLLER = '.content-scroll'
const MARGIN = 48

function topLevelBlock(view, pos) {
  const { node, offset } = view.domAtPos(pos)
  let el = node === view.dom ? view.dom.children[offset] ?? view.dom.lastElementChild : node
  while (el && el.parentElement !== view.dom) el = el.parentElement
  return el ?? null
}

function firstVisibleBlock(view, edge) {
  return [...view.dom.children].find((el) => el.getBoundingClientRect().bottom > edge) ?? null
}

export function useEditorLayout({ root, head, editor, source, enabled }) {
  const layout = ref('inline')
  // Whether the face has held focus since it opened: only then is its
  // selection a place the writer put the caret rather than the document start.
  let caretPlaced = false
  const editorFocus = inject('editorFocus', null)
  const { dialog } = useConfirm()

  function headBottom(scroller) {
    return head.value?.getBoundingClientRect().bottom ?? scroller.getBoundingClientRect().top
  }

  function richTarget(edge, focused) {
    const view = editor()?.view
    if (!view) return null
    const block = focused ? topLevelBlock(view, view.state.selection.from) : firstVisibleBlock(view, edge)
    return block ? () => block.getBoundingClientRect().top : null
  }

  function sourceTarget(edge, focused) {
    const el = source()?.textarea
    if (!el) return null
    if (focused) return () => caretPoint(el, el.selectionEnd).top
    const box = el.getBoundingClientRect()
    const ratio = box.height ? Math.min(Math.max((edge - box.top) / box.height, 0), 1) : 0
    return () => {
      const now = el.getBoundingClientRect()
      return now.top + ratio * now.height
    }
  }

  function pin() {
    const scroller = root.value?.closest(SCROLLER)
    if (!scroller) return () => {}
    const focused = caretPlaced
    const edge = headBottom(scroller)
    const top = richTarget(edge, focused) ?? sourceTarget(edge, focused)
    if (!top) return () => {}
    const before = top() - scroller.getBoundingClientRect().top
    return () => {
      source()?.fit?.()
      const origin = scroller.getBoundingClientRect().top
      const floor = headBottom(scroller) - origin
      const want = Math.min(Math.max(before, floor), scroller.clientHeight - MARGIN)
      scroller.scrollTop += top() - origin - want
    }
  }

  function onFocusin(event) {
    if (!head.value?.contains(event.target)) caretPlaced = true
  }

  // The button pressed may be the one the switch removes, which would drop
  // focus to the page; the writer pressed it to keep writing.
  function refocus() {
    const view = editor()?.view
    if (view) return view.focus()
    source()?.textarea?.focus({ preventScroll: true })
  }

  async function setLayout(next) {
    if (!enabled() || next === layout.value) return
    const restore = pin()
    layout.value = next
    await nextTick()
    restore()
    if (caretPlaced) refocus()
  }

  const toggle = () => setLayout(layout.value === 'focus' ? 'inline' : 'focus')

  function onKeydown(event) {
    const mod = event.metaKey || event.ctrlKey
    if (!enabled() || !mod || !event.shiftKey || event.altKey || event.key.toLowerCase() !== 'f') return
    event.preventDefault()
    toggle()
  }

  // On the document and in the capture phase, so Esc works wherever focus was
  // left on the page and before the rich face spends it on selecting the
  // parent node. While a menu, popover or dialog is open, Esc is theirs.
  function onDocumentKeydown(event) {
    if (event.key !== 'Escape' || dialog.visible) return
    if (document.querySelector('[data-dismissable-layer], [data-ref-menu], [data-slash-menu], [data-bubble-menu]')) return
    setLayout('inline')
  }

  watch(layout, (next) => {
    if (editorFocus) editorFocus.value = next === 'focus'
    const method = next === 'focus' ? 'addEventListener' : 'removeEventListener'
    document[method]('keydown', onDocumentKeydown, true)
  })

  onMounted(() => {
    root.value?.addEventListener('keydown', onKeydown)
    root.value?.addEventListener('focusin', onFocusin)
  })

  onBeforeUnmount(() => {
    root.value?.removeEventListener('keydown', onKeydown)
    root.value?.removeEventListener('focusin', onFocusin)
    document.removeEventListener('keydown', onDocumentKeydown, true)
    if (editorFocus && layout.value === 'focus') editorFocus.value = false
  })

  return { layout, setLayout }
}
