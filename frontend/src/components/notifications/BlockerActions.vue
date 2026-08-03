<script setup>
// What the operator can DO about a parked agent, from whatever page they are
// on. The options are the span's own (`blockers._options_of`), so the index
// sent back is the one the bridge will actually select — the previous banner
// re-parsed them out of the card's prose, which could only ever be shown, not
// clicked.
//
// Three shapes, decided by the server (`answerable`):
//   question → the listed options, one tap each (a pick is reversible in
//              conversation; making the operator confirm twice for a question
//              is the friction that sent them to /live in the first place)
//   decision → allow/deny behind a stage→confirm gate. NOT one tap: this one
//              runs a shell command on the agent's machine, and LiveQaDecision
//              gates it for the same reason.
//   null     → read-only. No channel reaches this session, so the options are
//              shown as text and the only real action is opening /live.
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import Button from '../ui/Button.vue'
import { useNotificationCenter } from '../../composables/useNotificationCenter'

const props = defineProps({
  row: { type: Object, required: true },
  compact: { type: Boolean, default: false },
})

const router = useRouter()
const { answerBlocker, decideBlocker } = useNotificationCenter()

const phase = ref('ready')
const detail = ref('')
const staged = ref('')

const sending = computed(() => phase.value === 'sending')
const isPlan = computed(() => props.row.kind === 'plan')
const canAnswer = computed(() => props.row.answerable === 'question')
const canDecide = computed(() => props.row.answerable === 'decision')
const options = computed(() => props.row.options || [])

const allowLabel = computed(() => (isPlan.value ? 'Approve plan' : 'Allow'))
const denyLabel = computed(() => (isPlan.value ? 'Reject plan' : 'Deny'))

function openLive() {
  const traceId = props.row.trace_id
  router.push(traceId ? `/live/${encodeURIComponent(traceId)}` : '/live')
}

async function send(run) {
  if (sending.value) return
  phase.value = 'sending'
  detail.value = ''
  const res = await run()
  if (res.delivered) return // the row is retired; this component unmounts
  phase.value = 'failed'
  detail.value = res.detail || 'send failed'
  staged.value = ''
}

function pick(option) {
  if (!canAnswer.value) return
  send(() => answerBlocker(props.row, option))
}

function confirmDecision() {
  const behavior = staged.value
  if (!behavior) return
  send(() => decideBlocker(props.row, behavior))
}
</script>

<template>
  <div class="blocker-actions" :class="{ 'blocker-actions-compact': compact }">
    <!-- Answerable question: the span's options, verbatim and in its order. -->
    <template v-if="canAnswer">
      <Button
        v-for="option in options"
        :key="option.index"
        size="sm"
        variant="secondary"
        class="blocker-opt-btn"
        :disabled="sending"
        :title="option.description || option.label"
        @click="pick(option)"
      >
        {{ option.label }}
      </Button>
    </template>

    <!-- Allow / deny: staged, then confirmed. -->
    <template v-else-if="canDecide">
      <template v-if="!staged">
        <Button size="sm" variant="primary" :disabled="sending" @click="staged = 'allow'">
          {{ allowLabel }}
        </Button>
        <Button size="sm" variant="secondary" :disabled="sending" @click="staged = 'deny'">
          {{ denyLabel }}
        </Button>
      </template>
      <template v-else>
        <span class="blocker-confirm-q">
          {{ staged === 'allow' ? allowLabel : denyLabel }} — sure?
        </span>
        <Button size="sm" variant="primary" :disabled="sending" @click="confirmDecision">
          {{ sending ? 'Sending…' : 'Confirm' }}
        </Button>
        <Button size="sm" variant="ghost" :disabled="sending" @click="staged = ''">
          Cancel
        </Button>
      </template>
    </template>

    <!-- No channel reaches this session: the options are context, not controls. -->
    <template v-else>
      <!-- Keyed on position, not `option.index`: a prose-recovered option has
           no index (it is `null` for every one of them), and a list of
           duplicate keys mis-patches on update. -->
      <span v-for="(option, at) in options" :key="at" class="blocker-opt-chip">
        {{ option.label }}
      </span>
    </template>

    <Button size="sm" variant="ghost" class="blocker-open-live" @click="openLive">
      Open live session →
    </Button>
  </div>

  <p v-if="phase === 'failed'" class="blocker-send-failed" role="status">
    Not delivered — {{ detail }}. The agent is still paused; open the live
    session to answer there.
  </p>
</template>

<style scoped>
.blocker-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.blocker-actions-compact { gap: 0.4rem; }

/* The mock's white pills: the card's own wash is the alert, so a filled
   button on top of it would compete with the banner rather than read as a
   choice inside it. */
.blocker-opt-btn {
  background: var(--color-surface);
  border-color: var(--color-amber-300);
  font-weight: 600;
}

.blocker-opt-btn:hover:not(:disabled) {
  background: var(--color-amber-50);
  border-color: var(--color-amber-500);
}

.blocker-opt-chip {
  font-size: 0.8rem;
  color: var(--color-fg);
  background: var(--color-surface);
  border: 1px solid var(--color-amber-300);
  border-radius: 0.5rem;
  padding: 0.35rem 0.65rem;
}

.blocker-confirm-q {
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--color-fg);
}

/* Pushed to the trailing edge so the real answers own the leading edge —
   the escape hatch should not be the first thing the eye lands on. */
.blocker-open-live {
  margin-inline-start: auto;
  color: var(--color-fg-muted);
}

.blocker-send-failed {
  margin: 0.5rem 0 0;
  font-size: 0.75rem;
  color: var(--color-danger);
}
</style>
