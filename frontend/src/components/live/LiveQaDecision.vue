<script setup>
// Decide surface for a pending permission/plan request, split by tier:
//
// - `sdkOwned`: Allow/Deny(+reason) for a call a regin-owned session parked —
//   the tier's own capability, since `can_use_tool` keeps the call open
//   until this POST resolves it. Unchanged from before the tmux tier existed.
// - tmux-observed (`!sdkOwned`): there is no typed channel, only a keystroke
//   into whatever widget is on screen, so the operator picks by POSITION —
//   from `tmuxOptions` when the request's hook payload carried real
//   suggestions, or from a fresh `bridge-menu` read (`tmuxLive`) when it
//   didn't (ExitPlanMode today; see `lib/agent_bridge/menu_parse.py`). Either
//   way the actual drive re-validates against the live pane server-side —
//   this UI never assumes a pick will land.
//
// Same two-step select→confirm gate throughout: a mis-tap here can run a
// shell command, or auto-accept a plan's edits, on the live agent's machine.
import { computed, onMounted, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import LiveQaGrowInput from './LiveQaGrowInput.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  kind: { type: String, default: 'tool' },
  // Names which parked call this decides (SDK tier only): a gated session
  // can be blocked on several at once (one assistant message, several tool
  // calls), and the card the operator tapped is not necessarily the oldest.
  toolUseId: { type: String, default: '' },
  sdkOwned: { type: Boolean, default: false },
  // Real per-option labels from the request's own span attrs (structured —
  // hook-captured `permission_suggestions`). `< 2` means the request carried
  // none, so the tmux branch falls back to a live pane read instead.
  tmuxOptions: { type: Array, default: () => [] },
  // Fetch a live-parsed menu on mount when `tmuxOptions` has no usable data.
  tmuxLive: { type: Boolean, default: false },
})
const emit = defineEmits(['decided'])

const phase = ref('ready')
const detail = ref('')
const sending = computed(() => phase.value === 'sending')

// ── SDK-owned: Allow/Deny + optional reason (unchanged) ──────
const staged = ref('')
const reason = ref('')
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

// ── tmux-observed: pick by position, structured or live-parsed ──
const usingLive = computed(() => props.tmuxOptions.length < 2)
const liveOptions = ref([])
const menuState = ref(usingLive.value
  ? (props.tmuxLive ? 'loading' : 'unavailable') : 'ready')
const menuDetail = ref('')
const pickOptions = computed(() => (usingLive.value
  ? liveOptions.value : props.tmuxOptions.map(o => o.label)))
const pickStaged = ref(null)

onMounted(async () => {
  if (props.sdkOwned || !usingLive.value || !props.tmuxLive) return
  let res = null
  try {
    res = await api.get(`/sessions/${props.sessionId}/bridge-menu`)
  } catch { res = null }
  const options = res && res.parsed ? (res.options || []) : []
  if (options.length >= 2) {
    liveOptions.value = options
    menuState.value = 'ready'
  } else {
    menuState.value = 'unavailable'
    menuDetail.value = (res && res.detail) || ''
  }
})

function stagePick(oi) {
  if (sending.value) return
  pickStaged.value = oi
}

function resetPick() {
  pickStaged.value = null
  if (!sending.value) { phase.value = 'ready'; detail.value = '' }
}

async function confirmPick() {
  if (pickStaged.value === null || sending.value) return
  phase.value = 'sending'
  detail.value = ''
  let res = null
  try {
    res = await api.post(`/sessions/${props.sessionId}/bridge-decide`, {
      option_index: pickStaged.value,
      label: pickOptions.value[pickStaged.value],
      live: usingLive.value || undefined,
    })
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
  <div v-if="sdkOwned" data-testid="live-qa-decision">
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

  <div v-else data-testid="live-qa-decision">
    <p v-if="menuState === 'loading'" class="live-qa-answer-meta"
       data-testid="live-qa-decide-loading">
      reading the live menu…
    </p>

    <p v-else-if="menuState === 'unavailable'" class="live-qa-answer-meta live-qa-failed"
       data-testid="live-qa-decide-unavailable">
      Can't be decided from here — resolve it in the terminal{{ menuDetail ? `: ${menuDetail}` : '.' }}
    </p>

    <template v-else>
      <!-- A raw <button>, not <Button>, matching LiveQaAnswer.vue's
           `live-qa-pick` option row exactly: full-width icon+label content
           the Button primitive's slot doesn't fit. Reset chrome, pointer,
           and :focus-visible all live on `button.live-qa-pick` in
           assets/style.css (~line 2733), applied globally, not per-file. -->
      <button
        v-for="(label, oi) in pickOptions" :key="oi" type="button"
        class="live-qa-opt live-qa-pick" :class="{ 'live-qa-staged': pickStaged === oi }"
        :disabled="sending || undefined"
        data-testid="live-qa-decide-pick" @click="stagePick(oi)"
      >
        <span class="live-qa-optmark">{{ pickStaged === oi ? '✓' : '›' }}</span>
        <span class="live-qa-optbody"><span class="live-qa-optlbl">{{ label }}</span></span>
      </button>

      <div v-if="pickStaged !== null" class="live-qa-confirm"
           data-testid="live-qa-decide-confirm">
        <div class="live-qa-confirm-hd">
          Send: <b>{{ pickOptions[pickStaged] }}</b>
        </div>
        <div class="live-qa-confirm-actions">
          <Button
            variant="ghost" size="sm" :disabled="sending"
            data-testid="live-qa-decide-cancel" @click="resetPick"
          >Cancel</Button>
          <Button
            variant="primary" size="sm" :disabled="sending"
            data-testid="live-qa-decide-send" @click="confirmPick"
          >Confirm & send</Button>
        </div>
      </div>

      <p class="live-qa-answer-meta" :class="{ 'live-qa-failed': phase === 'failed' }">
        <template v-if="phase === 'sending'">sending decision to the live agent…</template>
        <template v-else-if="phase === 'failed'">✗ {{ detail }}</template>
        <template v-else-if="pickStaged !== null">confirm to deliver, or cancel</template>
        <template v-else>the agent is blocked on this call until you decide</template>
      </p>
    </template>
  </div>
</template>
