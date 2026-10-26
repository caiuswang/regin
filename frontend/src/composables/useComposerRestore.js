import { ref, watch } from 'vue'

// "Restore to composer" on a dropped chip, carried to whichever composer is
// mounted for that session. The two sit in unrelated subtrees of the trace
// page, so a request is held here until a composer for the same session
// takes it — exactly once. The caller learns it was taken through `onTaken`
// and only then retires the chip: the chip is the text's last copy, and a
// request no composer ever takes must not have cost it.
const pending = ref(null)

export function restoreToComposer(sessionId, text, onTaken = () => {}) {
  if (!sessionId || !text) return
  pending.value = { sessionId, text, onTaken }
}

export function cancelComposerRestore() {
  pending.value = null
}

export function useComposerRestore(getSessionId, apply) {
  watch(() => [pending.value, getSessionId()], ([req, sid]) => {
    if (!req || !sid || req.sessionId !== sid) return
    pending.value = null
    apply(req.text)
    req.onTaken()
  }, { immediate: true })
}
