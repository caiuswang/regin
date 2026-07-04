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
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import LiveCommandMenu from './LiveCommandMenu.vue'
import { useSlashCommands } from '../../composables/useSlashCommands'

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

// Slash-command / skill autocomplete. The composable owns the popup state;
// this component only wires textarea events into it and applies accepted text.
const menu = useSlashCommands()
const { open: menuOpen, filtered: menuItems, activeIndex: menuActive } = menu
// The teleported menu is fixed-positioned above the textarea (recomputed each
// time the popup opens or the textarea grows).
const menuStyle = ref({})
function updateMenuPos() {
  const el = taEl.value
  if (!el) return
  const r = el.getBoundingClientRect()
  menuStyle.value = {
    left: `${r.left}px`,
    width: `${r.width}px`,
    bottom: `${window.innerHeight - r.top + 6}px`,
  }
}

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
    menu.close()
    if (taEl.value) taEl.value.style.height = 'auto'
    confirmTimer = setTimeout(() => { phase.value = 'ready' }, 2000)
  } else {
    // Structured refusal or HTTP failure: surface the server detail and
    // keep the draft so the user can retry.
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || 'send failed'
  }
}

// Recovery: inject an Escape keystroke into the pane. A harness overlay
// (slash-command help, a menu) swallows the composer's typed text, so a
// normal send fails its ack — one Esc dismisses the overlay so typing works
// again. Independent of the draft (nothing to type) and of `canSend`.
async function sendEsc() {
  if (delivering.value) return
  clearConfirmTimer()
  phase.value = 'delivering'
  let res = null
  try {
    res = await api.post(`/sessions/${props.sessionId}/bridge-key`, { key: 'Escape' })
  } catch { res = null }
  if (res && res.delivered) {
    phase.value = 'delivered'
    detail.value = res.detail || 'Esc sent'
    confirmTimer = setTimeout(() => { phase.value = 'ready' }, 2000)
  } else {
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || 'Esc failed'
  }
}

// Current caret offset in the textarea (end-of-text when unfocused/unknown).
function caret() {
  return taEl.value?.selectionStart ?? draft.value.length
}

// Recompute the popup on every draft change; lazily load the catalog once.
function onInput() {
  autogrow()
  menu.ensureLoaded(props.sessionId)
  menu.sync(draft.value, caret())
  if (menuOpen.value) updateMenuPos()
}

// Caret moved without editing (arrow keys, click): only re-evaluate an
// already-open menu so it dismisses when the caret leaves the leading
// `/token`. Never re-opens a closed menu — otherwise the keyup after Escape
// would immediately re-trigger it. Typing (onInput) is what opens it.
function onCaretSync() {
  if (!menuOpen.value) return
  menu.sync(draft.value, caret())
  if (menuOpen.value) updateMenuPos()
}

// Apply an accepted `/command ` draft and restore the caret after it.
function applyAccepted(text, pos) {
  draft.value = text
  nextTick(() => {
    const el = taEl.value
    if (el) { el.focus(); el.setSelectionRange(pos, pos) }
    autogrow()
  })
}

function onMenuSelect(item) {
  const res = menu.accept(draft.value, item)
  if (res) applyAccepted(res.text, res.caret)
}

function onKeydown(e) {
  // The open menu claims nav/accept/dismiss keys before send handling.
  const r = menu.handleKeydown(e, draft.value)
  if (r.handled) {
    e.preventDefault()
    if (r.text !== undefined) applyAccepted(r.text, r.caret)
    return
  }
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && canSend.value) send()
}
</script>

<template>
  <div class="live-composer" :class="{ 'live-composer-steer': steer }" data-testid="live-composer">
    <LiveCommandMenu
      v-if="menuOpen"
      :items="menuItems"
      :active-index="menuActive"
      :query="menu.query.value"
      :anchor-style="menuStyle"
      @select="onMenuSelect"
      @hover="menu.setActive"
    />
    <textarea
      ref="taEl"
      v-model="draft"
      class="live-composer-ta"
      rows="1"
      :placeholder="placeholder"
      :aria-label="steer ? 'Steering message for the running turn' : 'Prompt to send to this session'"
      :disabled="delivering"
      role="combobox"
      aria-autocomplete="list"
      aria-controls="live-command-menu"
      :aria-expanded="menuOpen"
      :aria-activedescendant="menuOpen ? `live-cmd-opt-${menuActive}` : undefined"
      data-testid="live-composer-ta"
      @input="onInput"
      @keydown="onKeydown"
      @keyup="onCaretSync"
      @click="onCaretSync"
      @blur="menu.close"
    ></textarea>
    <Button
      variant="ghost"
      size="sm"
      class="live-esc-btn"
      aria-label="Send Escape to dismiss a stuck terminal overlay"
      title="Dismiss a stuck overlay (sends Esc to the terminal)"
      :disabled="delivering"
      data-testid="live-composer-esc"
      @click="sendEsc"
    >esc</Button>
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
