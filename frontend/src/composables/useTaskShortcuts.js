import { onBeforeUnmount, onMounted } from 'vue'

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
    if (isTyping(event)) return
    const handler = handlers[event.key]
    if (!handler) return
    event.preventDefault()
    handler(event)
  }

  onMounted(() => window.addEventListener('keydown', onKeydown))
  onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
}
