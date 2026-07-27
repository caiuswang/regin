<script setup>
// Allow/deny surface for a permission request a regin-owned session parked —
// the operator surface behind LiveQaSheet when the span carries `kind: plan`
// or `kind: tool`. This is the tier's own capability: regin holds the SDK
// process, so `can_use_tool` keeps the call open until this POST resolves it.
//
// Same two-step select→confirm gate as LiveQaAnswer, for the same reason and
// more so: a mis-tap here runs a shell command on the live agent's machine.
// A denial may carry a reason — the SDK returns it to the model as the
// refusal message, so it is the operator's chance to say what to do instead.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import LiveQaGrowInput from './LiveQaGrowInput.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  kind: { type: String, default: 'tool' },
  // Names which parked call this decides: a gated session can be blocked on
  // several at once (one assistant message, several tool calls), and the card
  // the operator tapped is not necessarily the oldest.
  toolUseId: { type: String, default: '' },
})
const emit = defineEmits(['decided'])

const staged = ref('')
const reason = ref('')
const phase = ref('ready')
const detail = ref('')
const sending = computed(() => phase.value === 'sending')
const isPlan = computed(() => props.kind === 'plan')

const allowLabel = computed(() => (isPlan.value ? '✓ Approve plan' : '✓ Allow'))
const denyLabel = computed(() => (isPlan.value ? '✗ Reject plan' : '✗ Deny'))
const stagedLabel = computed(() => (staged.value === 'allow'
  ? allowLabel.value : denyLabel.value))

function stage(behavior) {
  if (sending.value) return
  staged.value = behavior
  reason.value = ''
}

function reset() {
  staged.value = ''
  reason.value = ''
  if (!sending.value) { phase.value = 'ready'; detail.value = '' }
}

function payload() {
  const note = reason.value.trim()
  return {
    behavior: staged.value,
    reason: note || undefined,
    tool_use_id: props.toolUseId || undefined,
  }
}

async function confirm() {
  if (!staged.value || sending.value) return
  phase.value = 'sending'
  detail.value = ''
  let res = null
  try {
    res = await api.post(`/sessions/${props.sessionId}/bridge-decide`, payload())
  } catch { res = null }
  if (res && res.delivered) {
    emit('decided')
  } else {
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || 'send failed'
  }
}
</script>

<template>
  <div data-testid="live-qa-decision">
    <div class="live-qa-verbs">
      <Button
        variant="primary" size="sm" :disabled="sending"
        data-testid="live-qa-allow" @click="stage('allow')"
      >{{ allowLabel }}</Button>
      <Button
        variant="danger" size="sm" :disabled="sending"
        data-testid="live-qa-deny" @click="stage('deny')"
      >{{ denyLabel }}</Button>
    </div>

    <div v-if="staged" class="live-qa-confirm" data-testid="live-qa-decide-confirm">
      <div class="live-qa-confirm-hd">
        Send decision: <b>{{ stagedLabel }}</b>
      </div>
      <!-- No @enter: the send is irreversible, so only the explicit Confirm
           button may fire it while a reason is being typed. -->
      <LiveQaGrowInput
        v-model="reason"
        :placeholder="staged === 'deny'
          ? 'Why — what should the agent do instead? (optional)'
          : 'Add a note for the agent (optional)…'"
        aria-label="Reason for this decision"
        :disabled="sending"
        testid="live-qa-decide-reason"
      />
      <div class="live-qa-confirm-actions">
        <Button
          variant="ghost" size="sm" :disabled="sending"
          data-testid="live-qa-decide-cancel" @click="reset"
        >Cancel</Button>
        <Button
          variant="primary" size="sm" :disabled="sending"
          data-testid="live-qa-decide-send" @click="confirm"
        >Confirm & send</Button>
      </div>
    </div>

    <p class="live-qa-answer-meta" :class="{ 'live-qa-failed': phase === 'failed' }">
      <template v-if="phase === 'sending'">sending decision to the live agent…</template>
      <template v-else-if="phase === 'failed'">✗ {{ detail }}</template>
      <template v-else-if="staged">confirm to deliver, or cancel</template>
      <template v-else-if="isPlan">approve to let the agent start building, or reject with a reason</template>
      <template v-else>the agent is blocked on this call until you decide</template>
    </p>
  </div>
</template>
