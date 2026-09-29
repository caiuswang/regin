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

// While a menu or dialog is open every key is that overlay's: reka closes on a
// window-level Escape only when nobody has called preventDefault on it. The
// target alone is not enough — a menu that opened a dialog hands focus back
// to its trigger, outside the dialog, so the key arrives from the page.
const OVERLAY = '[role="menu"], [role="dialog"], [role="alertdialog"], [role="listbox"]'
function inOverlay(event) {
  return !!event.target?.closest?.(OVERLAY)
    || !!document.querySelector(`:is(${OVERLAY})[data-state="open"]`)
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
