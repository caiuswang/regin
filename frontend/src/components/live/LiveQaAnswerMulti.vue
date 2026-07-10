<script setup>
// Interactive answerer for a PENDING MULTI-question ask over a reachable bridge
// (all questions single-select) — the operator surface behind LiveQaSheet when
// an ask carries more than one question.
//
// The claude select TUI shows one question at a time and lets a human step
// forward AND back to revise, so this mirrors that: the operator picks an answer
// per question (Prev/Next to move, change any freely), and NOTHING reaches the
// pane until "Send all". Then the answers are delivered in order to the SAME
// proven single-question endpoint — submitting question i makes the pane render
// question i+1 — each POST carrying `confirm_text` (that question) so the
// backend refuses if a failed advance left an earlier question focused. Collect-
// then-send is what makes "go back and change an answer" safe: revision happens
// in the card, before anything irreversible reaches the live agent.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import { askOptLabel, askOptDescription } from '../../utils/traceFormatters.js'

const props = defineProps({
  span: { type: Object, required: true },
  sessionId: { type: String, required: true },
})
const emit = defineEmits(['answered'])

const questions = computed(() => props.span?.attributes?.questions || [])
const total = computed(() => questions.value.length)
const idx = ref(0)
const q = computed(() => questions.value[idx.value] || null)
const options = computed(() => q.value?.options || [])

// answers[i] = { kind: 'option', index, label, note } | { kind: 'free', text } | null
const answers = ref(questions.value.map(() => null))
const cur = computed(() => answers.value[idx.value])

const phase = ref('ready') // ready | sending | failed
const detail = ref('')
const sendingLabel = ref('')
const sending = computed(() => phase.value === 'sending')

function pickOption(oi) {
  if (sending.value) return
  answers.value[idx.value] = { kind: 'option', index: oi, label: askOptLabel(options.value[oi]), note: '' }
}
function startFree() {
  if (sending.value) return
  answers.value[idx.value] = { kind: 'free', text: '' }
}

const curAnswered = computed(() => {
  const a = cur.value
  if (!a) return false
  return a.kind === 'option' || !!(a.text || '').trim()
})
const allAnswered = computed(() => answers.value.length > 0 && answers.value.every(a =>
  a && (a.kind === 'option' || !!(a.text || '').trim())))

function go(delta) {
  if (sending.value) return
  const n = idx.value + delta
  if (n >= 0 && n < total.value) idx.value = n
}

// Mirror LiveQaAnswer's single-question mapping, per question: a plain pick
// sends its option index; a note (the TUI has no note field) rides the "Type
// something." entry as `label — note`; a free answer types at that same index.
function payloadFor(i) {
  const a = answers.value[i]
  const qq = questions.value[i]
  const confirm_text = qq?.question || undefined
  const freeIndex = (qq?.options || []).length
  if (a.kind === 'free') return { option_index: freeIndex, text: a.text.trim(), confirm_text }
  const note = (a.note || '').trim()
  if (note) return { option_index: freeIndex, text: `${a.label} — ${note}`, label: a.label, confirm_text }
  return { option_index: a.index, label: a.label, confirm_text }
}

// One atomic POST: the backend walks every tab and presses Submit, verifying
// focus per question — so a dropped round-trip can never leave the ask half
// filled, and the operator's revisions all happened here before send.
async function sendAll() {
  if (!allAnswered.value || sending.value) return
  phase.value = 'sending'
  detail.value = ''
  sendingLabel.value = `Sending ${total.value} answers to the terminal…`
  const answers = questions.value.map((_, i) => payloadFor(i))
  let res = null
  try {
    res = await api.post(`/sessions/${props.sessionId}/bridge-answer`, { answers })
  } catch { res = null }
  if (res && res.delivered) {
    emit('answered')
  } else {
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || 'send failed'
  }
}
</script>

<template>
  <div data-testid="live-qa-answer-multi">
    <div class="live-qa-card">
      <div class="live-qa-card-hd">
        <div class="live-qa-step" data-testid="live-qa-step">Question {{ idx + 1 }} of {{ total }}</div>
        <div v-if="q?.header" class="live-qa-h">{{ q.header }}</div>
        <div class="live-qa-title">{{ q?.question }}</div>
      </div>
      <component
        :is="'button'"
        v-for="(opt, oi) in options"
        :key="oi"
        class="live-qa-opt live-qa-pick"
        :class="{ 'live-qa-staged': cur && cur.kind === 'option' && cur.index === oi }"
        :disabled="sending || undefined"
        data-testid="live-qa-pick"
        @click="pickOption(oi)"
      >
        <span class="live-qa-optmark">›</span>
        <span class="live-qa-optbody">
          <span class="live-qa-optlbl">{{ askOptLabel(opt) }}</span>
          <span v-if="askOptDescription(opt)" class="live-qa-optdesc">{{ askOptDescription(opt) }}</span>
        </span>
      </component>
      <component
        :is="'button'"
        class="live-qa-opt live-qa-pick"
        :class="{ 'live-qa-staged': cur && cur.kind === 'free' }"
        :disabled="sending || undefined"
        data-testid="live-qa-stage-free"
        @click="startFree"
      >
        <span class="live-qa-optmark">✎</span>
        <span class="live-qa-optbody"><span class="live-qa-optlbl">Type your own</span></span>
      </component>
    </div>

    <input
      v-if="cur && cur.kind === 'option'"
      v-model="cur.note"
      class="live-qa-free-input"
      type="text"
      placeholder="Add a note to this choice (optional)…"
      aria-label="Add a note to this choice"
      :disabled="sending"
      data-testid="live-qa-note-input"
    />
    <input
      v-else-if="cur && cur.kind === 'free'"
      v-model="cur.text"
      class="live-qa-free-input"
      type="text"
      placeholder="Type your own answer…"
      aria-label="Type your own answer"
      :disabled="sending"
      data-testid="live-qa-free-input"
    />

    <div class="live-qa-nav">
      <Button
        variant="ghost" size="sm" :disabled="sending || idx === 0"
        data-testid="live-qa-prev" @click="go(-1)"
      >← Previous</Button>
      <Button
        v-if="idx < total - 1"
        variant="ghost" size="sm" :disabled="sending || !curAnswered"
        data-testid="live-qa-next" @click="go(1)"
      >Next →</Button>
      <Button
        variant="primary" size="sm" class="live-qa-send" :disabled="sending || !allAnswered"
        data-testid="live-qa-send-all" @click="sendAll"
      >Send all answers</Button>
    </div>

    <p class="live-qa-answer-meta" :class="{ 'live-qa-failed': phase === 'failed' }">
      <template v-if="phase === 'sending'">{{ sendingLabel }}</template>
      <template v-else-if="phase === 'failed'">
        ✗ {{ detail }} · the ask is mid-way in the terminal — finish it directly there
      </template>
      <template v-else-if="allAnswered">all {{ total }} answered — Send all delivers them to the terminal in order</template>
      <template v-else>answer each question, then Send all — go back to change any before sending</template>
    </p>
  </div>
</template>
