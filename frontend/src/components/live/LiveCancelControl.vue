<script setup>
// Cancel the turn in flight, in the /live NOW zone. Unlike Stop, this works on
// BOTH tiers — the route picks the transport (a typed interrupt for a session
// regin owns, the Escape a human would press for one it merely traces) — so
// this component never has to know which it is looking at.
//
// One tap, no confirm, deliberately unlike LiveStopControl: Escape is one
// keypress in a terminal and this is what it does there, the session survives
// either way, and a two-step confirm on the control you reach for when the
// agent is already off down the wrong path is friction in exactly the wrong
// place. The cancellation is asynchronous like a Stop — the turn ending in the
// tail is the real confirmation, not this line.
import { ref, computed, onUnmounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
})

const phase = ref('ready') // ready | cancelling | cancelled | failed
const detail = ref('')
let resetTimer = null

const cancelling = computed(() => phase.value === 'cancelling')

function clearResetTimer() {
  if (resetTimer) { clearTimeout(resetTimer); resetTimer = null }
}
onUnmounted(clearResetTimer)

function reset() {
  clearResetTimer()
  detail.value = ''
  phase.value = 'ready'
}

async function cancel() {
  if (cancelling.value) return
  clearResetTimer()
  phase.value = 'cancelling'
  let res = null
  try {
    res = await api.post(`/sessions/${props.sessionId}/bridge-interrupt`, {})
  } catch { res = null }
  if (res && res.delivered) {
    phase.value = 'cancelled'
    detail.value = res.detail || 'interrupt sent'
    // Back to a button on its own: unlike Stop there is nothing terminal about
    // a cancel, and a second one is a legitimate thing to want.
    resetTimer = setTimeout(reset, 3000)
  } else {
    // A refusal is the informative case — "no reachable session" or "bridge
    // disabled" is what the operator needs to read, not a silent no-op.
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || 'cancel failed'
  }
}
</script>

<template>
  <div class="live-cancel" data-testid="live-cancel">
    <Button
      v-if="phase === 'ready'"
      variant="link"
      size="sm"
      class="live-cancel-btn"
      aria-label="Cancel the turn the agent is running"
      title="Cancel this turn — the session stays open for the next prompt"
      data-testid="live-cancel-btn"
      @click="cancel"
    >⎋ cancel turn</Button>

    <template v-else-if="phase === 'cancelling'">
      <span class="live-spinner live-spinner-sm" aria-hidden="true"></span>
      <span class="live-cancel-meta">cancelling…</span>
    </template>

    <span
      v-else
      class="live-cancel-meta"
      :class="{ 'live-cancel-failed': phase === 'failed' }"
      data-testid="live-cancel-detail"
    >{{ phase === 'cancelled' ? `✓ ${detail}` : `✗ ${detail}` }}</span>

    <!-- A refusal must not be terminal: a dropped request or a session this
         process briefly couldn't reach would otherwise leave the operator with
         no way back to the button short of reloading the card. -->
    <Button
      v-if="phase === 'failed'"
      variant="link"
      size="sm"
      class="live-cancel-again"
      data-testid="live-cancel-retry"
      @click="reset"
    >try again</Button>
  </div>
</template>
