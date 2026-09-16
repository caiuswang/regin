import { onScopeDispose, ref } from 'vue'
import { useFlash } from './useFlash.js'

const COPIED_MS = 1400

// Clipboard copy whose acknowledgement is rendered by the caller, in the
// affordance the user just clicked — a toast for something that happened under
// the pointer reads as an unrelated interruption, and in a dialog it lands
// behind the backdrop. Only a *failure* still flashes: that one needs words.
//
// `copied` is keyed by the copied text (override with an explicit `key`) so a
// component holding several copy buttons lights up only the one that was hit.
export function useCopy() {
  const { flash } = useFlash()
  const copiedKey = ref(null)
  let timer = null

  async function copyText(text, key = text) {
    if (!text) return false
    if (!await writeClipboard(text)) {
      flash('Copy failed', 'error')
      return false
    }
    copiedKey.value = key
    clearTimeout(timer)
    timer = setTimeout(() => { copiedKey.value = null }, COPIED_MS)
    return true
  }

  function isCopied(key) {
    return copiedKey.value !== null && copiedKey.value === key
  }

  onScopeDispose(() => clearTimeout(timer))
  return { copyText, copiedKey, isCopied }
}

// navigator.clipboard is undefined outside a secure context — exactly the
// mobile case, where the dashboard is reached over plain http on a LAN IP.
// Fall back to a hidden-textarea execCommand copy so the button still works.
async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    try {
      return document.execCommand('copy')
    } finally {
      ta.remove()
    }
  }
}
