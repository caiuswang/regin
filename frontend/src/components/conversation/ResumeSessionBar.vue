<script setup>
// What sits where the follow-up composer would, for a session that is over but
// that the CLI can still reopen. The two are mutually exclusive by
// construction: `bridge_reachable` and `resumable` are computed from one
// server-side branch, so a session is steerable OR continuable, never both.
//
// This exists because the ordinary way an SDK run ends is not an ending at
// all: stopping `regin serve` takes the child with it, and the operator is
// left on a conversation with no composer, no pane, and — until now — no way
// back into it except finding the id again in the /live launch sheet.
//
// Resume reuses the same route the sheet posts to, with the session's own cwd
// and model: `claude --resume` resolves the id relative to the working
// directory, and a continuation that silently changed model is not something
// the operator asked for.
//
// It does NOT navigate. The resumed run keeps the child's session id
// (`fork_session=False`), so the conversation continues under the id this page
// is already open on — walking the operator to /live would move them off the
// thread they are reading to show them the same one. Instead the launch emits
// `resumed`, and the host reloads: the server clears `ended_at` before it
// answers, which is what re-arms the suspended live poll and swaps this bar
// for the composer in place.
import { ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  // { cwd, model, endedReason } off the session row — the launch inputs plus
  // the reason to show for why this conversation stopped.
  context: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['resumed'])
const busy = ref(false)
const refusal = ref('')

async function resume() {
  if (busy.value) return
  busy.value = true
  refusal.value = ''
  try {
    const data = await api.post('/agent-runs', {
      resume: props.sessionId,
      cwd: props.context.cwd || undefined,
      model: props.context.model || undefined,
    })
    if (!data.launched) {
      refusal.value = data.detail || 'Resume refused.'
      busy.value = false
      return
    }
    // Stays busy on the way out, with no `finally` to clear it: the host's
    // reload is what retires this bar, and re-enabling the button in that gap
    // would offer a second launch of a session that is now live.
    emit('resumed')
  } catch (e) {
    refusal.value = e?.message || 'Resume failed.'
    busy.value = false
  }
}
</script>

<template>
  <div class="resume-bar" data-testid="resume-session-bar">
    <div class="resume-bar-copy">
      <Icon name="refresh-cw" :size="16" class="shrink-0 opacity-70" />
      <span>
        This session is no longer running<template v-if="context.endedReason">
          — {{ context.endedReason }}</template>. Resume it to keep the same
        conversation.
      </span>
    </div>
    <Button
      variant="secondary"
      size="sm"
      :disabled="busy"
      data-testid="resume-session-button"
      @click="resume"
    >{{ busy ? 'Resuming…' : 'Resume session' }}</Button>
    <p v-if="refusal" class="resume-bar-refusal" role="alert">{{ refusal }}</p>
  </div>
</template>

<style scoped>
.resume-bar {
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
.resume-bar-copy {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex: 1 1 16rem;
  min-width: 0;
}
.resume-bar-refusal {
  flex: 1 1 100%;
  margin: 0;
  color: var(--color-danger);
}
</style>
