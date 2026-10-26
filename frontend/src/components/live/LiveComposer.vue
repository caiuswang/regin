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
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import LiveCommandMenu from './LiveCommandMenu.vue'
import LiveCtxMeter from './LiveCtxMeter.vue'
import ComposerHighlight from '../conversation/ComposerHighlight.vue'
import { useSlashCommands } from '../../composables/useSlashCommands'
import { useFileMentions } from '../../composables/useFileMentions'
import { useImagePaste, imageFilesFrom } from '../../composables/useImagePaste'
import { useSuccessorJump } from '../../composables/useSuccessorJump'
import { useRefusedSend } from '../../composables/useRefusedSend'
import RefusedSendNotice from '../conversation/RefusedSendNotice.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  steer: { type: Boolean, default: false },
  pane: { type: String, default: '' },
  // Segment-aware live-peak ctx% — the second surface for the header's ctx
  // meter, right-aligned on the bridge-meta line (amber past 80%).
  ctxPct: { type: Number, default: null },
})

const draft = defineModel('draft', { type: String, default: '' })
const phase = ref('ready') // ready | delivering | delivered | failed
const detail = ref('')
const taEl = ref(null)
const hlEl = ref(null)
const fileEl = ref(null)
let confirmTimer = null

// Autocomplete: `/` commands+skills and `@` file mentions. Both composables own
// their popup state; this component only wires textarea events into whichever
// one is active and applies the accepted text. An `@` mention outranks `/`
// (a draft can hold both, and the mention is where the caret is).
const { jumpTo } = useSuccessorJump()
const cmds = useSlashCommands()
const files = useFileMentions()
const active = computed(() => (files.open.value ? files : cmds.open.value ? cmds : null))
const menuOpen = computed(() => active.value !== null)
const fileMode = computed(() => files.open.value)
const menuItems = computed(() => (active.value ? active.value.filtered.value : []))
const menuActive = computed(() => (active.value ? active.value.activeIndex.value : 0))
const menuQuery = computed(() => (active.value ? active.value.query.value : ''))
const menuLoading = computed(() => (active.value ? active.value.loading.value : false))
const menuStale = computed(() => (active.value ? active.value.stale.value : false))
// aria-activedescendant must name a rendered `role=option`; the loading and
// empty branches of the menu render none, so it is emitted only when the list
// actually has rows.
const activeOptionId = computed(() => (
  menuOpen.value && menuActive.value < menuItems.value.length
    ? `live-cmd-opt-${menuActive.value}`
    : undefined))

// The teleported menu is fixed-positioned above the textarea. Recomputed on
// open/input AND on resize while open — it is `position: fixed`, so anything
// that moves the composer would otherwise leave it stranded. The capture-phase
// `scroll` half is purely defensive and currently unreachable: at both 375px
// and desktop the only scrollable ancestor is chrome that does not move the
// composer. Kept so a future layout that does scroll it cannot strand the menu.
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
function onViewportShift() { if (menuOpen.value) updateMenuPos() }
function trackViewport(on) {
  const fn = on ? window.addEventListener : window.removeEventListener
  fn.call(window, 'scroll', onViewportShift, true)
  fn.call(window, 'resize', onViewportShift)
}
watch(menuOpen, trackViewport)
onUnmounted(() => trackViewport(false))

function closeMenus() {
  cmds.close()
  files.close()
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
  syncHighlight()
}

// The mirror is inert and so has no scrollbar of its own: past the 88px cap it
// only stays on its tokens while the field's scroll offset is copied onto it.
function syncHighlight() {
  const el = hlEl.value?.$el
  if (el && taEl.value) el.scrollTop = taEl.value.scrollTop
}

function clearConfirmTimer() {
  if (confirmTimer) { clearTimeout(confirmTimer); confirmTimer = null }
}
onUnmounted(clearConfirmTimer)
// A remount may start with a preserved draft — size the textarea to it.
onMounted(autogrow)

// The row id is what ties a send to the server log and to the inbox row the
// runner may later stamp `dropped:` — without it a prompt that went missing
// left the operator nothing to quote.
function logSend(res, chars) {
  console.info('[bridge] send', {
    row: res?.id ?? null,
    delivered: !!res?.delivered,
    detail: res?.detail || res?.msg || 'no response',
    chars,
  })
}

async function send() {
  const text = draft.value.trim()
  if (!text || delivering.value) return
  clearConfirmTimer()
  phase.value = 'delivering'
  let res = null
  try {
    res = await api.post(`/sessions/${props.sessionId}/bridge-send`, { text })
  } catch { res = null }
  logSend(res, text.length)
  if (res && res.delivered) {
    refused.clear()
    phase.value = 'delivered'
    detail.value = res.detail || 'delivered'
    // No optimistic chip: a delivered steer is represented server-side from
    // the moment of delivery (SDK queue entry or pending bridge row), so the
    // next poll serves the real thing; the "delivered" flash covers the gap.
    draft.value = ''
    images.clear()
    closeMenus()
    if (taEl.value) taEl.value.style.height = 'auto'
    confirmTimer = setTimeout(() => { phase.value = 'ready' }, 2000)
    jumpTo(props.sessionId, res.trace_id)
  } else {
    // Structured refusal or HTTP failure: surface the server detail and
    // keep the draft so the user can retry.
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || 'send failed'
    if (refused.take(res)) detail.value = 'not sent — this task run has finished'
  }
}

const refused = useRefusedSend()
watch(() => props.sessionId, () => refused.clear())

async function resumeRefused() {
  if (!(await refused.resumeWith(draft.value.trim()))) return
  draft.value = ''
  images.clear()
  phase.value = 'delivered'
  detail.value = 'session resumed — your message is its next prompt'
}

async function postRefusedAsComment() {
  if (!(await refused.postAsComment(draft.value.trim()))) return
  draft.value = ''
  images.clear()
  phase.value = 'ready'
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

// Resolve which popup (if any) the caret is in. Mentions get first refusal;
// only when there is no live `@` token does the leading-`/` menu get a say.
function syncMenus() {
  files.ensureLoaded(props.sessionId)
  files.sync(draft.value, caret())
  if (files.open.value) { cmds.close(); return }
  cmds.ensureLoaded(props.sessionId)
  cmds.sync(draft.value, caret())
}

// Recompute the popup on every draft change; lazily load the catalog once.
function onInput() {
  autogrow()
  syncMenus()
  if (menuOpen.value) updateMenuPos()
}

// Caret moved without editing (arrow keys, click): only re-evaluate an
// already-open menu so it dismisses when the caret leaves its token. Never
// re-opens a closed menu — otherwise the keyup after Escape would immediately
// re-trigger it. Typing (onInput) is what opens it.
function onCaretSync() {
  if (!menuOpen.value) return
  syncMenus()
  if (menuOpen.value) updateMenuPos()
}

// Images pasted, dropped or picked into this composer. The markers it hands
// back are spliced at the caret like an accepted mention, and cleared only
// once the draft they belong to has actually been delivered.
const images = useImagePaste()
const stagedImages = images.staged
const imgUploading = images.uploading
watch(() => props.sessionId, id => images.bind(id), { immediate: true })

function insertAtCaret(markers) {
  if (!markers.length) return
  const pos = caret()
  const token = `${markers.join(' ')} `
  const next = draft.value.slice(0, pos) + token + draft.value.slice(pos)
  applyAccepted(next, pos + token.length)
}

async function onPaste(e) {
  // Only claim the event when it actually carries an image — a text paste
  // must keep the browser's own insert behaviour.
  if (!imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  insertAtCaret(await images.stage(e.clipboardData))
}

async function onDrop(e) {
  if (!imageFilesFrom(e.dataTransfer).length) return
  e.preventDefault()
  insertAtCaret(await images.stage(e.dataTransfer))
}

// Removing a chip must also remove its marker, or the delivered text would
// name an image the prompt no longer carries.
function dropImage(img) {
  draft.value = draft.value.replace(img.marker, '').replace(/\s{2,}/g, ' ').trimStart()
  images.remove(img.idx)
}

async function onPick(e) {
  insertAtCaret(await images.stage(e.target))
  e.target.value = ''
}

// Apply an accepted draft and restore the caret after the insert (a mention
// mid-draft must not fling the cursor to the end).
function applyAccepted(text, pos) {
  draft.value = text
  nextTick(() => {
    const el = taEl.value
    if (el) { el.focus(); el.setSelectionRange(pos, pos) }
    autogrow()
    if (menuOpen.value) updateMenuPos()
  })
}

// An Enter/Tab pressed while the mention list was stale is armed rather than
// dropped; when the rows for that same query land, the accept it stands for is
// applied here — the composable defers because the draft text lives here.
watch(files.armedResolved, () => {
  const res = files.accept(draft.value)
  if (res) applyAccepted(res.text, res.caret)
})

function onMenuSelect(item) {
  const m = active.value
  const res = m && m.accept(draft.value, item)
  if (res) applyAccepted(res.text, res.caret)
}

function onMenuHover(i) {
  if (active.value) active.value.setActive(i)
}

function onKeydown(e) {
  // Mid-composition keys belong to the IME: a CJK user pressing Enter to commit
  // a candidate must not have it swallowed as a menu accept.
  if (e.isComposing || e.keyCode === 229) return
  // The open menu claims nav/accept/dismiss keys before send handling.
  const m = active.value
  const r = m ? m.handleKeydown(e, draft.value) : { handled: false }
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
      :query="menuQuery"
      :loading="menuLoading"
      :stale="menuStale"
      :prefix="fileMode ? '@' : '/'"
      :aria-label="fileMode ? 'Project files and directories' : 'Slash commands and skills'"
      :empty-text="fileMode ? 'no file matches' : 'no command matches'"
      :anchor-style="menuStyle"
      @select="onMenuSelect"
      @hover="onMenuHover"
    />
    <div
      v-if="stagedImages.length"
      class="live-attach-strip"
      data-testid="live-composer-attachments"
    >
      <span
        v-for="img in stagedImages"
        :key="img.idx"
        class="live-attach-chip"
      >
        <img :src="img.url" :alt="img.name" class="live-attach-thumb">
        <span class="live-attach-idx">#{{ img.idx }}</span>
        <Button
          variant="ghost"
          size="icon"
          class="live-attach-x"
          :aria-label="`Remove image ${img.idx}`"
          @click="dropImage(img)"
        >&times;</Button>
      </span>
    </div>
    <div class="live-composer-field">
      <ComposerHighlight ref="hlEl" class="live-composer-hl" :text="draft" />
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
      :aria-controls="menuOpen ? 'live-command-menu' : undefined"
      :aria-expanded="menuOpen"
      :aria-activedescendant="activeOptionId"
      data-testid="live-composer-ta"
      @input="onInput"
      @keydown="onKeydown"
      @keyup="onCaretSync"
      @click="onCaretSync"
      @paste="onPaste"
      @dragover.prevent
      @drop="onDrop"
      @blur="closeMenus"
      @scroll="syncHighlight"
    ></textarea>
    </div>
    <input
      ref="fileEl"
      type="file"
      accept="image/*"
      class="sr-only"
      tabindex="-1"
      aria-hidden="true"
      data-testid="live-composer-file"
      @change="onPick"
    >
    <Button
      variant="ghost"
      size="icon"
      class="live-attach-btn"
      aria-label="Attach an image"
      title="Attach an image"
      :disabled="delivering || imgUploading"
      data-testid="live-composer-attach"
      @click="fileEl?.click()"
    >
      <Icon name="image" :size="15" />
    </Button>
    <Button
      v-if="pane"
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
  <RefusedSendNotice :refused="refused" @post="postRefusedAsComment" @resume="resumeRefused" />
  <div class="live-bridge-row">
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
        <template v-if="pane">bridge · <span class="live-mono">{{ pane }}</span></template>
        <template v-else>agent session</template>
        · {{ idleMeta }}
      </span>
    </div>
    <LiveCtxMeter :pct="ctxPct" verbose />
  </div>
</template>
