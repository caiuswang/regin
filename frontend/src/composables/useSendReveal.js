import { ref, watch } from 'vue'
import { useFocusMode } from './useFocusMode.js'

// A follow-up send is the one moment the reader has said what they want to
// look at next, so it is the only thing allowed to close turns they opened by
// hand. Armed by the composer's `sent`, spent by the next turn to arrive — the
// arriving span is the trigger, never a timer.
//
// "The next turn to arrive" is deliberately not "the turn this send produced":
// a bridged prompt carries no id back that the span it eventually becomes can
// be matched against, and a text match would break the moment the harness
// rewrote the prompt. The cost is that a send the agent silently dropped stays
// armed, and the next turn from anywhere — one typed straight into the
// terminal — spends it. That turn is still the one the reader is waiting on,
// so the arm is scoped by session rather than by prompt: a trace change
// disarms it, because clearing another session's transcript is not.
export function useSendReveal({ expandedPromptIds, isExempt, traceId }) {
  const { openFold } = useFocusMode()
  const armed = ref(false)

  function arm() { armed.value = true }

  watch(traceId, () => { armed.value = false })

  // A no-op until armed, so the caller can stay one unconditional statement
  // inside a watcher that runs on every poll.
  function applyTo(keepId) {
    if (!armed.value) return
    armed.value = false
    for (const id of [...expandedPromptIds.value]) {
      if (id !== keepId && !isExempt(id)) expandedPromptIds.value.delete(id)
    }
    openFold(keepId)
  }

  return { arm, applyTo }
}
