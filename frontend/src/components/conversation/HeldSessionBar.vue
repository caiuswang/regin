<script setup>
// The third state under a transcript, and the reason it needed a component of
// its own: "steerable" and "resumable" are both false here, which on its own
// renders an empty rail that reads like a broken page. It is neither. The
// conversation is alive — the Claude CLI is holding it on a terminal regin has
// no handle on (its background daemon runs one on a private PTY), so there is
// no composer to offer and nothing to resume.
//
// Take over is on demand rather than on sight: attaching every background
// session the machine has would hold a `claude attach` client and a tmux pane
// open for each of them forever, against the chance someone opens its page.
// Pressing this is the operator saying they want to type into THIS one.
//
// It does not navigate, and it does not restart anything. `claude attach` is a
// PTY client — the conversation keeps its own pid inside the CLI's daemon —
// so all this changes is that a pane regin owns is now showing it. The host
// reloads on `attached`, the server then answers `bridge_reachable`, and the
// composer replaces this bar in place.
import { ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
})

const emit = defineEmits(['attached'])
const busy = ref(false)
const refusal = ref('')

async function takeOver() {
  if (busy.value) return
  busy.value = true
  refusal.value = ''
  try {
    const data = await api.post(`/sessions/${props.sessionId}/attach`, {})
    if (!data.attached) {
      refusal.value = data.detail || 'Could not take over this session.'
      busy.value = false
      return
    }
    // Stays busy on the way out, with no `finally`: the host's reload retires
    // this bar, and re-enabling the button in that gap would offer a second
    // take-over of a session that now has a pane.
    emit('attached')
  } catch (e) {
    refusal.value = e?.message || 'Could not take over this session.'
    busy.value = false
  }
}
</script>

<template>
  <div class="held-bar" data-testid="held-session-bar">
    <div class="held-bar-copy">
      <Icon name="message-square" :size="16" class="shrink-0 opacity-70" />
      <span>Still running under the Claude CLI, on a terminal regin can't type into.</span>
    </div>
    <Button
      variant="secondary"
      size="sm"
      :disabled="busy"
      data-testid="held-session-take-over"
      @click="takeOver"
    >{{ busy ? 'Taking over…' : 'Take over' }}</Button>
    <p v-if="refusal" class="held-bar-refusal" role="alert">{{ refusal }}</p>
  </div>
</template>

<style scoped>
.held-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.625rem;
  padding: 0.625rem 0.875rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
  font-size: 0.8125rem;
  color: var(--color-fg-muted);
}
.held-bar-copy {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex: 1 1 16rem;
  min-width: 0;
}
.held-bar-refusal {
  flex: 1 1 100%;
  margin: 0;
  color: var(--color-danger);
}
</style>
