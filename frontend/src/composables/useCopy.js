import { useFlash } from './useFlash.js'

// Clipboard copy with the standard "Copied!" flash. Shared by every
// conversation card that surfaces a Copy affordance (prompt, assistant
// response, bash command/output, diff, tool failure, server-tool reply).
export function useCopy() {
  const { flash } = useFlash()
  async function copyText(text) {
    if (!text) return
    if (await writeClipboard(text)) flash('Copied!')
    else flash('Copy failed', 'error')
  }
  return { copyText }
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
