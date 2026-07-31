<script setup>
// Stop for a regin-owned (SDK tier) session, in the /live NOW zone. Only this
// tier can be stopped from the browser: a session regin merely traces has no
// channel to end it through, so the control is absent there rather than a
// button that stops nothing.
//
// Two-step inline confirm rather than window.confirm: the card is a phone
// surface, and a native dialog is both unstyleable there and untestable.
// Stopping is asynchronous by design — the server acknowledges the request,
// the run's teardown lands a poll or two later — so this reports what the
// route said and then gets out of the way; the session going quiet is the
// real confirmation, exactly as with a sent prompt.
import { ref, computed, onUnmounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
})

const phase = ref('ready') // ready | confirming | stopping | stopped | failed
const detail = ref('')
let resetTimer = null

const stopping = computed(() => phase.value === 'stopping')

function clearResetTimer() {
  if (resetTimer) { clearTimeout(resetTimer); resetTimer = null }
}
onUnmounted(clearResetTimer)

function arm() {
  clearResetTimer()
  detail.value = ''
  phase.value = 'confirming'
  // An armed confirm that is walked away from must not stay armed: a stray
  // later tap would then stop the agent with no intent behind it.
  resetTimer = setTimeout(() => { phase.value = 'ready' }, 6000)
}

function cancel() {
  clearResetTimer()
  phase.value = 'ready'
}

async function stop() {
  if (stopping.value) return
  clearResetTimer()
  phase.value = 'stopping'
  let res = null
  try {
    res = await api.post(`/agent-runs/${props.sessionId}/stop`, {})
  } catch { res = null }
  if (res && res.delivered) {
    phase.value = 'stopped'
    detail.value = res.detail || 'stopping'
  } else {
    // A refusal is the informative case — "no live agent session" means this
    // process no longer holds the run, which is what the operator needs to
    // read rather than a silent success.
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || 'stop failed'
  }
}
</script>

<template>
  <div class="live-stop" data-testid="live-stop">
    <template v-if="phase === 'ready'">
      <Button
        variant="link"
        size="sm"
        class="live-stop-btn"
        aria-label="Stop this agent session"
        title="End this run — the current turn is interrupted"
        data-testid="live-stop-arm"
        @click="arm"
      >■ stop agent</Button>
    </template>

    <template v-else-if="phase === 'confirming'">
      <span class="live-stop-ask">stop this agent?</span>
      <Button
        variant="link"
        size="sm"
        class="live-stop-btn live-stop-confirm"
        data-testid="live-stop-confirm"
        @click="stop"
      >yes, stop</Button>
      <Button
        variant="link"
        size="sm"
        class="live-stop-cancel"
        data-testid="live-stop-cancel"
        @click="cancel"
      >cancel</Button>
    </template>

    <template v-else-if="phase === 'stopping'">
      <span class="live-spinner live-spinner-sm" aria-hidden="true"></span>
      <span class="live-stop-meta">stopping…</span>
    </template>

    <span
      v-else
      class="live-stop-meta"
      :class="{ 'live-stop-failed': phase === 'failed' }"
      data-testid="live-stop-detail"
    >{{ phase === 'stopped' ? `✓ ${detail}` : `✗ ${detail}` }}</span>
    <!-- A refusal must not be terminal: a dropped request or a run this
         process briefly couldn't reach would otherwise leave the operator with
         no way back to the button short of reloading the card. -->
    <Button
      v-if="phase === 'failed'"
      variant="link"
      size="sm"
      class="live-stop-cancel"
      data-testid="live-stop-retry"
      @click="cancel"
    >try again</Button>
  </div>
</template>
