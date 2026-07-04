<script setup>
// Bridge composer for the /live NOW zone (v5): POST the typed text to the
// web-JWT proxy (/api/sessions/<id>/bridge-send), which delivers it into
// the session's registered tmux pane server-side. Two variants: full-width
// idle (starts the next turn) and compact steer (queues into the running
// turn). This child owns ALL the async send state so LiveNowZone stays a
// pure projection of the tail.
//
// Delivery contract: {delivered:true} → clear + brief "✓ <detail>" confirm;
// {delivered:false} or an HTTP error → visible failure line surfacing the
// server's `detail`, textarea re-enabled with the TEXT PRESERVED for retry.
// The sent prompt is never appended client-side — it appears in the tail
// only when the poll returns the real promptlive-/prompt span. That is also
// why an unmount right after a send loses nothing: the delivered prompt's
// span arriving in the tail IS the confirmation — no in-composer state to
// preserve.
//
// The draft is a `v-model:draft` owned by LiveNowZone (always mounted), so
// text typed mid-draft survives this component unmounting — a reachability
// blip for one poll, or the state flipping to question/permission — and is
// restored on remount.
import { ref, computed, onMounted, onUnmounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  steer: { type: Boolean, default: false },
  pane: { type: String, default: '' },
})

const draft = defineModel('draft', { type: String, default: '' })
const phase = ref('ready') // ready | delivering | delivered | failed
const detail = ref('')
const taEl = ref(null)
let confirmTimer = null

const delivering = computed(() => phase.value === 'delivering')
const canSend = computed(() => !!draft.value.trim() && !delivering.value)
const placeholder = computed(() => (props.steer
  ? 'Steer the agent — lands mid-turn…'
  : 'Send a prompt to this session…'))
const idleMeta = computed(() => (props.steer
  ? 'queues into the running turn'
  : 'starts the next turn'))

// Autogrow: height follows content up to the 88px cap. The parent view's
// ResizeObserver on the NOW zone re-pins the tail on every height change.
function autogrow() {
  const el = taEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 88)}px`
}

function clearConfirmTimer() {
  if (confirmTimer) { clearTimeout(confirmTimer); confirmTimer = null }
}
onUnmounted(clearConfirmTimer)
// A remount may start with a preserved draft — size the textarea to it.
onMounted(autogrow)

async function send() {
  const text = draft.value.trim()
  if (!text || delivering.value) return
  clearConfirmTimer()
  phase.value = 'delivering'
  let res = null
  try {
    res = await api.post(`/sessions/${props.sessionId}/bridge-send`, { text })
  } catch { res = null }
  if (res && res.delivered) {
    phase.value = 'delivered'
    detail.value = res.detail || 'delivered'
    draft.value = ''
    if (taEl.value) taEl.value.style.height = 'auto'
    confirmTimer = setTimeout(() => { phase.value = 'ready' }, 2000)
  } else {
    // Structured refusal or HTTP failure: surface the server detail and
    // keep the draft so the user can retry.
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || 'send failed'
  }
}

function onKeydown(e) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && canSend.value) send()
}
</script>

<template>
  <div class="live-composer" :class="{ 'live-composer-steer': steer }" data-testid="live-composer">
    <textarea
      ref="taEl"
      v-model="draft"
      class="live-composer-ta"
      rows="1"
      :placeholder="placeholder"
      :aria-label="steer ? 'Steering message for the running turn' : 'Prompt to send to this session'"
      :disabled="delivering"
      data-testid="live-composer-ta"
      @input="autogrow"
      @keydown="onKeydown"
    ></textarea>
    <Button
      variant="primary"
      size="icon"
      class="live-send-btn"
      aria-label="Send via bridge"
      :disabled="!canSend"
      data-testid="live-composer-send"
      @click="send"
    >
      <Icon name="arrow-up" :size="15" />
    </Button>
  </div>
  <div
    class="live-bridge-meta"
    :class="{
      'live-bridge-delivering': phase === 'delivering',
      'live-bridge-delivered': phase === 'delivered',
      'live-bridge-failed': phase === 'failed',
    }"
    data-testid="live-bridge-meta"
  >
    <template v-if="phase === 'delivering'">
      <span class="live-spinner live-spinner-sm" aria-hidden="true"></span>
      <span>delivering via bridge…</span>
    </template>
    <span v-else-if="phase === 'delivered'">✓ {{ detail }}</span>
    <span v-else-if="phase === 'failed'">✗ {{ detail }}</span>
    <span v-else>
      bridge<template v-if="pane"> · <span class="live-mono">{{ pane }}</span></template>
      · {{ idleMeta }}
    </span>
  </div>
</template>
