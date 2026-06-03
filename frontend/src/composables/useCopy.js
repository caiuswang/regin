import { useFlash } from './useFlash.js'

// Clipboard copy with the standard "Copied!" flash. Shared by every
// conversation card that surfaces a Copy affordance (prompt, assistant
// response, bash command/output, diff, tool failure, server-tool reply).
export function useCopy() {
  const { flash } = useFlash()
  function copyText(text) {
    if (!text) return
    navigator.clipboard.writeText(text).then(() => flash('Copied!'))
  }
  return { copyText }
}
