import { onActivated, onBeforeUnmount, onDeactivated, onMounted } from 'vue'

// A chord typed into a field is text, not a command. Checked against the event
// target rather than a flag, so a shortcut can never fire out of a composer
// that happens to be open behind a portal.
function isTyping(event) {
  const el = event.target
  if (!el) return false
  const tag = (el.tagName || '').toLowerCase()
  return tag === 'input' || tag === 'textarea' || tag === 'select'
    || el.isContentEditable
}

// A key pressed inside an open menu or dialog is that overlay's: reka closes
// on a window-level Escape only when nobody has called preventDefault on it.
function inOverlay(event) {
  return !!event.target?.closest?.('[role="menu"], [role="dialog"], [role="alertdialog"], [role="listbox"]')
}

/**
 * useTaskShortcuts — the single-key chords the list answers to.
 *
 * `handlers` is a map of key → callback. A key with a modifier held is left
 * alone: `⌘C` is copy, and stealing it to open a composer is the kind of
 * shortcut that gets a page muted.
 */
export function useTaskShortcuts(handlers) {
  function onKeydown(event) {
    if (event.metaKey || event.ctrlKey || event.altKey) return
    if (isTyping(event) || inOverlay(event)) return
    const handler = handlers[event.key]
    if (!handler) return
    event.preventDefault()
    handler(event)
  }

  // The Pipeline tabs are kept alive, so a tab you left is deactivated, not
  // unmounted — its chords must stop with it, or `c` on Features also opens
  // the Tasks composer behind it.
  let bound = false
  const bind = () => { if (!bound) window.addEventListener('keydown', onKeydown); bound = true }
  const unbind = () => { window.removeEventListener('keydown', onKeydown); bound = false }
  onMounted(bind)
  onActivated(bind)
  onDeactivated(unbind)
  onBeforeUnmount(unbind)
}
