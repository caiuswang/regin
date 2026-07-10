<script setup>
// Interactive answerer for a PENDING single-question ask over a reachable
// bridge — the operator surface behind LiveQaSheet. Mirrors the three verbs
// the real claude select TUI actually offers (pick an option · "Type
// something." · "Chat about this"), each behind a two-step select→confirm
// gate so a mis-tap can't answer the live agent irreversibly.
//
// Delivery mapping (what reaches the pane, all via POST bridge-answer):
//  - option, no note  → {option_index: i, label}                  (plain pick)
//  - option + note    → {option_index: freeIndex, text:'label — note'}  the TUI
//                        cannot attach a note to a pick, so a noted choice is
//                        delivered through the "Type something." entry
//  - free text        → {option_index: freeIndex, text}
//  - chat about this   → {option_index: chatIndex, text?, chat:true}  navigates
//                        to the "Chat about this" entry, dismissing the menu
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import LiveQaGrowInput from './LiveQaGrowInput.vue'
import { askOptLabel, askOptDescription } from '../../utils/traceFormatters.js'

const props = defineProps({
  span: { type: Object, required: true },
  sessionId: { type: String, required: true },
})
const emit = defineEmits(['answered'])

const q0 = computed(() => (props.span?.attributes?.questions || [])[0] || null)
const options = computed(() => q0.value?.options || [])
// The "Type something." free-text entry sits right after the listed options;
// "Chat about this" sits one past that (below the TUI's divider).
const freeIndex = computed(() => options.value.length)
const chatIndex = computed(() => options.value.length + 1)

// staged = null | { kind: 'option'|'free'|'chat', index, label }
const staged = ref(null)
const note = ref('')
const freeText = ref('')
const chatText = ref('')
const phase = ref('ready') // ready | sending | failed
const detail = ref('')
const sending = computed(() => phase.value === 'sending')

function reset() {
  staged.value = null
  note.value = ''
  freeText.value = ''
  chatText.value = ''
  if (phase.value !== 'sending') { phase.value = 'ready'; detail.value = '' }
}
function stageOption(oi) {
  if (sending.value) return
  staged.value = { kind: 'option', index: oi, label: askOptLabel(options.value[oi]) }
  note.value = ''
}
function stageFree() {
  if (sending.value) return
  staged.value = { kind: 'free', index: freeIndex.value, label: '' }
}
function stageChat() {
  if (sending.value) return
  staged.value = { kind: 'chat', index: chatIndex.value, label: 'Chat about this' }
}

const canConfirm = computed(() => {
  const s = staged.value
  if (!s || sending.value) return false
  if (s.kind === 'free') return !!freeText.value.trim()
  return true // option (note optional) · chat (message optional)
})

function buildPayload() {
  const s = staged.value
  if (s.kind === 'option') {
    const n = note.value.trim()
    if (n) return { option_index: freeIndex.value, text: `${s.label} — ${n}`, label: s.label }
    return { option_index: s.index, label: s.label }
  }
  if (s.kind === 'free') return { option_index: freeIndex.value, text: freeText.value.trim() }
  const m = chatText.value.trim()
  return { option_index: chatIndex.value, text: m || undefined, label: 'Chat about this', chat: true }
}

async function confirm() {
  if (!canConfirm.value) return
  phase.value = 'sending'
  detail.value = ''
  let res = null
  try {
    res = await api.post(`/sessions/${props.sessionId}/bridge-answer`, buildPayload())
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
  <div data-testid="live-qa-answer">
    <div class="live-qa-card">
      <div class="live-qa-card-hd">
        <div v-if="q0?.header" class="live-qa-h">{{ q0.header }}</div>
        <div class="live-qa-title">{{ q0?.question }}</div>
      </div>
      <div v-for="(opt, oi) in options" :key="oi" class="live-qa-optwrap">
        <component
          :is="'button'"
          class="live-qa-opt live-qa-pick"
          :class="{ 'live-qa-staged': staged && staged.kind === 'option' && staged.index === oi }"
          :disabled="sending || undefined"
          data-testid="live-qa-pick"
          @click="stageOption(oi)"
        >
          <span class="live-qa-optmark">›</span>
          <span class="live-qa-optbody">
            <span class="live-qa-optlbl">{{ askOptLabel(opt) }}</span>
            <span v-if="askOptDescription(opt)" class="live-qa-optdesc">{{ askOptDescription(opt) }}</span>
          </span>
        </component>
        <details v-if="opt && opt.preview" class="live-qa-preview" data-testid="live-qa-preview">
          <summary>Preview</summary>
          <pre>{{ opt.preview }}</pre>
        </details>
      </div>
    </div>

    <div class="live-qa-verbs">
      <Button
        variant="ghost" size="sm" :disabled="sending"
        data-testid="live-qa-stage-free" @click="stageFree"
      >✎ Type your own</Button>
      <Button
        variant="ghost" size="sm" :disabled="sending"
        data-testid="live-qa-stage-chat" @click="stageChat"
      >💬 Chat about this</Button>
    </div>

    <div v-if="staged" class="live-qa-confirm" data-testid="live-qa-confirm">
      <div class="live-qa-confirm-hd">
        <template v-if="staged.kind === 'option'">Send answer: <b>{{ staged.label }}</b></template>
        <template v-else-if="staged.kind === 'free'">Type your own answer</template>
        <template v-else>Chat about this — the menu closes and your message goes to the agent</template>
      </div>
      <!-- No @enter here: a note is optional garnish on an already-staged
           pick, and the send is irreversible — only the explicit Confirm
           button may fire it while a note is being typed. -->
      <LiveQaGrowInput
        v-if="staged.kind === 'option'"
        v-model="note"
        placeholder="Add a note to this choice (optional)…"
        aria-label="Add a note to this choice" :disabled="sending"
        testid="live-qa-note-input"
      />
      <p v-if="staged.kind === 'option' && note.trim()" class="live-qa-confirm-hint">
        The terminal has no note field, so this is sent as a typed answer: “{{ staged.label }} — {{ note.trim() }}”.
      </p>
      <LiveQaGrowInput
        v-else-if="staged.kind === 'free'"
        v-model="freeText"
        placeholder="Type your own answer…" aria-label="Type your own answer"
        :disabled="sending" testid="live-qa-free-input"
        @enter="confirm"
      />
      <LiveQaGrowInput
        v-else
        v-model="chatText"
        placeholder="Message for the agent (optional)…" aria-label="Chat message"
        :disabled="sending" testid="live-qa-chat-input"
        @enter="confirm"
      />
      <div class="live-qa-confirm-actions">
        <Button
          variant="ghost" size="sm" :disabled="sending"
          data-testid="live-qa-cancel" @click="reset"
        >Cancel</Button>
        <Button
          variant="primary" size="sm" :disabled="!canConfirm"
          data-testid="live-qa-confirm-send" @click="confirm"
        >Confirm & send</Button>
      </div>
    </div>

    <p class="live-qa-answer-meta" :class="{ 'live-qa-failed': phase === 'failed' }">
      <template v-if="phase === 'sending'">sending answer to the live agent…</template>
      <template v-else-if="phase === 'failed'">✗ {{ detail }}</template>
      <template v-else-if="staged">confirm to deliver via the bridge, or cancel</template>
      <template v-else>pick an option, type your own, or chat — then confirm before it's sent</template>
    </p>
  </div>
</template>
