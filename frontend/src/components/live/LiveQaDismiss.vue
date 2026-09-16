<script setup>
// Decline a pending ask from the /live QA sheet — the "I am not answering
// this" verb the card had no affordance for. Every other control here answers
// the question; the NOW zone's cancel is gated off the question park on
// purpose (it is a turn-level control), so without this an operator parked on
// an ask could only answer it or leave the agent blocked.
//
// Routed through `bridge-interrupt`, which serves BOTH tiers — a typed
// interrupt for a session regin owns, the Escape a human would press for one
// it merely traces — so this never has to know which it is looking at. That
// route cancels the TURN, not just the question: the copy below says so,
// because a danger-variant button that under-promises its blast radius is
// worse than no button.
import { ref, computed } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
})
const emit = defineEmits(['dismissed'])

const phase = ref('ready') // ready | confirming | sending | failed
const detail = ref('')
const sending = computed(() => phase.value === 'sending')

function ask() {
  if (sending.value) return
  phase.value = 'confirming'
  detail.value = ''
}
function abort() {
  if (sending.value) return
  phase.value = 'ready'
  detail.value = ''
}

async function dismiss() {
  if (sending.value) return
  phase.value = 'sending'
  detail.value = ''
  let res = null
  try {
    res = await api.post(`/sessions/${props.sessionId}/bridge-interrupt`, {})
  } catch { res = null }
  if (res && res.delivered) {
    emit('dismissed')
  } else {
    // A refusal is the informative case — "no reachable session" or "bridge
    // disabled" is what the operator needs to read, not a silent no-op.
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || 'dismiss failed'
  }
}
</script>

<template>
  <div class="live-qa-dismiss" data-testid="live-qa-dismiss">
    <Button
      v-if="phase === 'ready'"
      variant="link"
      size="sm"
      aria-label="Decline this question and cancel the turn"
      title="Decline — the question closes and the turn in flight is cancelled"
      data-testid="live-qa-dismiss-open"
      @click="ask"
    >⎋ Don't answer this</Button>

    <div v-else class="live-qa-confirm" data-testid="live-qa-dismiss-confirm">
      <div class="live-qa-confirm-hd">
        Decline without answering? This cancels the turn in flight — the
        question closes and the agent stops what it was doing. The session
        stays open for your next prompt.
      </div>
      <p v-if="phase === 'failed'" class="live-qa-answer-meta live-qa-failed">
        ✗ {{ detail }}
      </p>
      <div class="live-qa-confirm-actions">
        <Button
          variant="ghost" size="sm" :disabled="sending"
          data-testid="live-qa-dismiss-abort" @click="abort"
        >Keep answering</Button>
        <Button
          variant="danger" size="sm" :disabled="sending"
          data-testid="live-qa-dismiss-send" @click="dismiss"
        >{{ sending ? 'Cancelling…' : (phase === 'failed' ? 'Try again' : 'Decline & cancel turn') }}</Button>
      </div>
    </div>
  </div>
</template>
