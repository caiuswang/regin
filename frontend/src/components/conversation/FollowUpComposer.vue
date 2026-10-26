<script setup>
// Follow-up composer, the last row of the conversation feed column — a sibling
// below the transcript's scroll region, never an overlay over it. Same bridge
// contract as the /live NOW zone's LiveComposer: POST the text to
// /api/sessions/<id>/bridge-send, which delivers it into the session's
// registered pane server-side. {delivered:true} → clear + a brief confirm;
// anything else → a visible failure line with the TEXT PRESERVED for retry.
// The prompt is never appended client-side — it reaches the feed only when the
// live poll returns its real span.
//
// The parent mounts this only for a session that is both live and bridgeable,
// so there is no disabled state to render.
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Textarea from '../ui/Textarea.vue'
import LiveCommandMenu from '../live/LiveCommandMenu.vue'
import ComposerRunControls from './ComposerRunControls.vue'
import TraceTerminalPeek from '../trace/TraceTerminalPeek.vue'
import ComposerHighlight from './ComposerHighlight.vue'
import { useSlashCommands } from '../../composables/useSlashCommands'
import { useFileMentions } from '../../composables/useFileMentions'
import { useImagePaste, imageFilesFrom } from '../../composables/useImagePaste'
import { useSuccessorJump } from '../../composables/useSuccessorJump'
import { useRefusedSend } from '../../composables/useRefusedSend'
import { useComposerRestore } from '../../composables/useComposerRestore'
import RefusedSendNotice from './RefusedSendNotice.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  turnNumber: { type: Number, default: 0 },
  // The session's follow-up context — { model, ctxPct, bridgeSession } — taken
  // as ONE object rather than as three scalars, read straight off the prop so
  // no unpacking layer can go stale. A host cannot forward a
  // subset of an object, which is what kept the two mounts in sync: the
  // docked one used to be handed `model` alone, so it silently lost the
  // terminal peek and the ctx reading the feed's copy showed.
  context: { type: Object, default: () => ({}) },
  // What the follow-up is *about*, when the host has a narrower subject than
  // the session as a whole (the span reader: "about prompt").
  contextLabel: { type: String, default: '' },
  placeholder: { type: String, default: 'Ask for a change, or send the next instruction…' },
  // A host that already owns a Reply affordance passes false: the resting
  // bubble would then be a second, redundant way in — and that host owns
  // dismissal too, since its own Escape has to be sequenced against ours.
  bubble: { type: Boolean, default: true },
})

// Expanded state is a model so a host with its own trigger can drive it while
// this component — and therefore the draft — stays mounted across the flip.
const open = defineModel('open', { type: Boolean, default: false })
const emit = defineEmits(['sent'])
const { jumpTo } = useSuccessorJump()
const bubbleWide = ref(false)
const draft = ref('')
const phase = ref('ready') // ready | delivering | delivered | failed
const detail = ref('')
const rootEl = ref(null)
const taComp = ref(null)
const fileEl = ref(null)
const taEl = computed(() => taComp.value?.$el || null)
const hlComp = ref(null)

// The mirror has no scrollbar of its own (it is inert), so it only stays
// aligned while the field's scroll offset is copied onto it.
function syncHighlight() {
  const el = hlComp.value?.$el
  if (el && taEl.value) el.scrollTop = taEl.value.scrollTop
}
let confirmTimer = null

// Autocomplete: `/` commands+skills and `@` file mentions. An `@` mention
// outranks `/` (a draft can hold both, and the mention is where the caret is).
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

function closeMenus() {
  cmds.close()
  files.close()
}

const delivering = computed(() => phase.value === 'delivering')
const canSend = computed(() => !!draft.value.trim() && !delivering.value)

function clearConfirmTimer() {
  if (confirmTimer) { clearTimeout(confirmTimer); confirmTimer = null }
}

function expand() { open.value = true }
function collapse() { open.value = false }

// Keyed off the model, not the trigger: a host driving `open` itself gets the
// same caret placement and the same teardown as the resting bubble does.
watch(open, async (v) => {
  if (!v) { closeMenus(); return }
  await nextTick()
  taEl.value?.focus()
})

// An OPEN composer is a wall across the bottom of the feed, and the three
// controls floating there (back-to-top, follow-latest, the app's quick-capture
// bubble) are anchored to the VIEWPORT, so they land on top of it — over the
// model chip and the Send button both.
//
// What they need is the panel's TOP measured from the viewport's bottom, not
// the panel's height: the dock does not start at the viewport's edge (the rail
// and the scroller's own inset sit below it), so a height-derived offset lands
// short and the overlap survives by however much that inset is. Publishing the
// top makes the band self-describing, and consumers take `max()` of it and the
// other bands rather than summing — the dock already sits above them.
//
// Only while OPEN: the resting bubble is a corner, not a wall, and reserving
// its full band would push all three up a third of the screen for nothing.
const DOCK_H = '--trace-dock-h'
let dockRo = null

function clearDockBand() {
  document.documentElement.style.removeProperty(DOCK_H)
}

function publishDockBand() {
  const dock = rootEl.value
  const panel = dock?.querySelector('.followup-panel')
  // Read the dock's own computed position rather than re-stating the width the
  // stylesheet already decides it at: above that breakpoint the dock scrolls
  // with the feed, covers nothing, and a band published there would only shove
  // three controls a panel's height up the page for no reason.
  if (!panel || getComputedStyle(dock).position !== 'sticky') return clearDockBand()
  const band = Math.max(0, Math.round(window.innerHeight - panel.getBoundingClientRect().top))
  document.documentElement.style.setProperty(DOCK_H, `${band}px`)
}

watch(open, async (v) => {
  dockRo?.disconnect()
  dockRo = null
  if (!v || !props.bubble) return clearDockBand()
  await nextTick()
  const panel = rootEl.value?.querySelector('.followup-panel')
  if (!panel) return clearDockBand()
  publishDockBand()
  if (typeof ResizeObserver === 'function') {
    // The dock as well as the panel: the dock's width changes on every resize,
    // which is what re-reads its position across the sticky breakpoint.
    dockRo = new ResizeObserver(publishDockBand)
    dockRo.observe(panel)
    dockRo.observe(rootEl.value)
  }
}, { immediate: true })

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
    refused.clear()
    phase.value = 'delivered'
    detail.value = res.detail || 'delivered'
    draft.value = ''
    images.clear()
    closeMenus()
    emit('sent')
    jumpTo(props.sessionId, res.trace_id)
    // The confirm window doubles as the panel's exit: the delivered line is
    // the only acknowledgement a send gets until the poll returns its real
    // span, so it has to stay readable, and once it has been read the panel is
    // just transcript the reader can no longer see. `open` is a model, so a
    // host with its own trigger sees its own state flip back with it.
    confirmTimer = setTimeout(() => {
      phase.value = 'ready'
      collapse()
    }, 2000)
  } else {
    phase.value = 'failed'
    detail.value = res?.detail || res?.msg || 'send failed'
    if (refused.take(res)) detail.value = 'not sent — this task run has finished'
  }
}

const refused = useRefusedSend()
watch(() => props.sessionId, () => refused.clear())

async function postRefusedAsComment() {
  if (!(await refused.postAsComment(draft.value.trim()))) return
  draft.value = ''
  images.clear()
  phase.value = 'ready'
}

// Only the page's own composer takes a restored chip; a span reader's docked
// copy of it would race it for the same session.
if (props.bubble) {
  useComposerRestore(() => props.sessionId, (text) => {
    draft.value = draft.value.trim() ? `${draft.value.trimEnd()}\n${text}` : text
    expand()
    nextTick(syncHighlight)
  })
}

function caret() {
  return taEl.value?.selectionStart ?? draft.value.length
}

function syncMenus() {
  files.ensureLoaded(props.sessionId)
  files.sync(draft.value, caret())
  if (files.open.value) { cmds.close(); return }
  cmds.ensureLoaded(props.sessionId)
  cmds.sync(draft.value, caret())
}

function onInput() {
  syncMenus()
  if (menuOpen.value) updateMenuPos()
  nextTick(syncHighlight)
}

// Caret moved without editing: only re-evaluate an already-open menu, never
// re-open a closed one (the keyup after Escape would re-trigger it).
function onCaretSync() {
  if (!menuOpen.value) return
  syncMenus()
  if (menuOpen.value) updateMenuPos()
}

// Images pasted, dropped or picked into this composer. Same composable the
// /live card uses, so the two surfaces cannot drift on what a paste means.
const images = useImagePaste()
const stagedImages = images.staged
const imgUploading = images.uploading
watch(() => props.sessionId, id => images.bind(id), { immediate: true })

function insertAtCaret(markers) {
  if (!markers.length) return
  const pos = caret()
  const token = `${markers.join(' ')} `
  applyAccepted(draft.value.slice(0, pos) + token + draft.value.slice(pos),
                pos + token.length)
}

async function onPaste(e) {
  // A text paste must keep the browser's own insert behaviour.
  if (!imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  insertAtCaret(await images.stage(e.clipboardData))
}

async function onDrop(e) {
  if (!imageFilesFrom(e.dataTransfer).length) return
  e.preventDefault()
  insertAtCaret(await images.stage(e.dataTransfer))
}

async function onPick(e) {
  insertAtCaret(await images.stage(e.target))
  e.target.value = ''
}

// Removing a chip must also remove its marker, or the delivered text would
// name an image the prompt no longer carries.
function dropImage(img) {
  draft.value = draft.value.replace(img.marker, '').replace(/\s{2,}/g, ' ').trimStart()
  images.remove(img.idx)
}

function applyAccepted(text, pos) {
  draft.value = text
  nextTick(() => {
    const el = taEl.value
    if (el) { el.focus(); el.setSelectionRange(pos, pos) }
    if (menuOpen.value) updateMenuPos()
    syncHighlight()
  })
}

// An Enter/Tab pressed while the mention list was stale is armed rather than
// dropped; the accept it stands for is applied here once the rows land.
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

// The attach affordance is the mention menu: drop an `@` at the caret and the
// file list opens on it, so there is one file-picking path, not two.
function attach() {
  const pos = caret()
  applyAccepted(`${draft.value.slice(0, pos)}@${draft.value.slice(pos)}`, pos + 1)
  nextTick(onInput)
}

function onTextareaKeydown(e) {
  if (e.isComposing || e.keyCode === 229) return
  const m = active.value
  const r = m ? m.handleKeydown(e, draft.value) : { handled: false }
  if (r.handled) {
    e.preventDefault()
    // A key the menu consumed is spent. Without this a host that also listens
    // for it — the span reader's Escape, which retires the composer — acts on
    // the same press, so one Escape dismisses the list AND the composer.
    e.stopPropagation()
    if (r.text !== undefined) applyAccepted(r.text, r.caret)
    return
  }
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && canSend.value) send()
  else if (e.key === 'Escape' && props.bubble) collapse()
}

const TYPING_TAGS = ['INPUT', 'TEXTAREA', 'SELECT']
function isTypingTarget(el) {
  if (!el) return false
  return TYPING_TAGS.includes(el.tagName) || el.isContentEditable
}

// Page-level shortcuts. Anything typed inside the composer is the textarea's
// own business (handled above); anything typed in another field is not ours.
function onWindowKeydown(e) {
  if (rootEl.value?.contains(e.target) || isTypingTarget(e.target)) return
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); expand() }
  else if (e.key === 'Escape' && open.value && props.bubble) collapse()
}

onMounted(() => window.addEventListener('keydown', onWindowKeydown))

onUnmounted(() => {
  window.removeEventListener('keydown', onWindowKeydown)
  trackViewport(false)
  clearConfirmTimer()
  dockRo?.disconnect()
  clearDockBand()
})
</script>

<template>
  <div
    ref="rootEl"
    class="followup-dock"
    :class="{ 'is-docked': !bubble }"
    data-testid="followup-composer"
  >
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
    <div v-if="!open && bubble" class="followup-bubble-row">
      <!-- Focus expands it as hover does: the label carries the shortcut, so a
           keyboard reader would otherwise never see what ⌘⏎ does here. -->
      <Button
        variant="ghost"
        size="md"
        class="followup-bubble"
        :class="{ 'is-open': bubbleWide }"
        title="Send a follow-up turn (⌘⏎)"
        aria-label="Open follow-up composer"
        data-testid="followup-pill"
        @click="expand"
        @mouseenter="bubbleWide = true"
        @mouseleave="bubbleWide = false"
        @focus="bubbleWide = true"
        @blur="bubbleWide = false"
      >
        <span class="followup-bubble-gloss"></span>
        <span class="inline-flex h-5 w-5 shrink-0 items-center justify-center">
          <Icon name="message-square" :size="19" />
        </span>
        <span class="followup-bubble-label">
          Follow up
          <kbd class="rounded-[5px] bg-white/[0.16] px-1.5 py-0.5 font-mono text-[10px] font-medium text-white/[0.78]">⌘⏎</kbd>
        </span>
        <span class="followup-bubble-dot"></span>
      </Button>
    </div>
    <div v-else-if="open" class="followup-panel">
      <div class="flex items-center gap-2 px-3.5 pt-2.5">
        <span class="followup-dot"></span>
        <span class="shrink-0 whitespace-nowrap text-[10px] font-bold uppercase tracking-[0.09em] text-slate-400">Continue session</span>
        <!-- min-w-0 + truncate, not a wrap: the row's three labels and the
             dismiss × are each single-line by intent, and at 375px their
             natural widths plus the gaps exceed the panel — the second line
             that bought read as a collision with the ×. -->
        <span class="min-w-0 flex-1 truncate font-mono text-[11px] text-slate-400">
          turn #{{ turnNumber }}<template v-if="contextLabel"> · {{ contextLabel }}</template><template v-if="context.ctxPct != null"> · ctx {{ Math.round(context.ctxPct) }}% used</template>
        </span>
        <TraceTerminalPeek :session="context.bridgeSession" />
        <Button
          variant="ghost"
          size="icon"
          class="ml-auto h-6 w-6 shrink-0 rounded-[7px] text-slate-400"
          title="Dismiss (esc)"
          aria-label="Dismiss composer"
          @click="collapse"
        ><Icon name="x" :size="14" /></Button>
      </div>
      <RefusedSendNotice :refused="refused" @post="postRefusedAsComment" />
      <div class="followup-field">
        <ComposerHighlight ref="hlComp" class="followup-hl" :text="draft" />
        <Textarea
        ref="taComp"
        v-model="draft"
        :rows="3"
        class="followup-ta"
        :placeholder="placeholder"
        aria-label="Follow-up prompt for this session"
        :disabled="delivering"
        role="combobox"
        aria-autocomplete="list"
        :aria-controls="menuOpen ? 'live-command-menu' : undefined"
        :aria-expanded="menuOpen"
        data-testid="followup-textarea"
        @input="onInput"
        @keydown="onTextareaKeydown"
        @keyup="onCaretSync"
        @click="onCaretSync"
        @paste="onPaste"
        @dragover.prevent
        @drop="onDrop"
        @blur="closeMenus"
        @scroll="syncHighlight"
      />
      </div>
      <div
        v-if="stagedImages.length"
        class="followup-attach-strip"
        data-testid="followup-attachments"
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
      <input
        ref="fileEl"
        type="file"
        accept="image/*"
        class="sr-only"
        tabindex="-1"
        aria-hidden="true"
        data-testid="followup-file"
        @change="onPick"
      >
      <div class="followup-actions flex flex-wrap items-center gap-[7px] pb-2.5 pl-3 pr-2.5">
        <span v-if="context.model" class="followup-chip">{{ context.model }}</span>
        <Button
          variant="ghost"
          size="icon"
          class="h-7 w-7 rounded-lg text-slate-400"
          title="Attach files"
          aria-label="Attach files"
          data-testid="followup-attach"
          @click="attach"
        ><Icon name="paperclip" :size="16" /></Button>
        <Button
          variant="ghost"
          size="icon"
          class="h-7 w-7 rounded-lg text-slate-400"
          title="Attach an image"
          aria-label="Attach an image"
          :disabled="delivering || imgUploading"
          data-testid="followup-attach-image"
          @click="fileEl?.click()"
        ><Icon name="image" :size="16" /></Button>
        <!-- The run controls sit with the composer because they answer the
             same question it does — "the agent is going the wrong way, now
             what" — and reaching a Cancel should not mean leaving the field
             you were about to redirect it from. -->
        <ComposerRunControls :session-id="sessionId" />
        <!-- Prose among controls, so it is the only thing here that gives.
             `ml-auto` is gone with it: a flex-basis of 0 both pushes Send to
             the edge and keeps this from being the item whose natural width
             forces the whole row onto a second line. -->
        <span class="followup-status truncate font-mono text-[10.5px]" :class="{
          'text-emerald-600': phase === 'delivered',
          'text-red-600': phase === 'failed',
          'text-slate-300': phase === 'ready' || phase === 'delivering',
          'followup-hint': phase === 'ready',
        }" data-testid="followup-status">
          <template v-if="phase === 'delivering'">delivering via bridge…</template>
          <template v-else-if="phase === 'delivered'">✓ {{ detail }}</template>
          <template v-else-if="phase === 'failed'">✗ {{ detail }}</template>
          <template v-else>⌘⏎ to send</template>
        </span>
        <Button
          variant="primary"
          size="sm"
          class="ml-auto h-8 shrink-0 gap-1.5 rounded-[10px] px-3.5"
          aria-label="Send follow-up via bridge"
          :disabled="!canSend"
          data-testid="followup-send"
          @click="send"
        >Send<Icon name="arrow-right" :size="15" /></Button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* A sibling BELOW the feed's scroll region, not an overlay inside it: nothing
   passes under the dock, so it separates with a rule rather than fading the
   text out under itself. */
.followup-dock {
  flex-shrink: 0;
  padding-top: 12px;
  border-top: 1px solid var(--color-border);
}

/* Below the bounded-layout threshold the shell scrolls, so the dock has to
   stick to stay reachable. It can afford to: the resting state is a corner
   bubble, so what it covers is a corner, and the fade only has to carry the
   few pixels of transcript that pass under it. */
@media (max-width: 1199px) {
  .followup-dock:not(.is-docked) {
    position: sticky;
    bottom: 0;
    z-index: 12;
    border-top: 0;
    padding-top: 0;
    background: linear-gradient(
      to top,
      color-mix(in srgb, var(--color-surface) 92%, transparent) 40%,
      color-mix(in srgb, var(--color-surface) 0%, transparent)
    );
  }

  .followup-dock:not(.is-docked):has(.followup-panel) {
    background: var(--color-surface);
    border-top: 1px solid var(--color-border);
    padding-top: 12px;
  }
}

/* Docked into a host that supplies its own frame and its own trigger: no rule
   of our own, and nothing at all rendered while collapsed. */
.followup-dock.is-docked {
  padding-top: 0;
  border-top: 0;
}

.followup-bubble-row {
  display: flex;
  justify-content: flex-end;
  padding: 6px 2px 10px;
}

/* Four things claim the bottom of a phone screen here: back-to-top (left),
   the feed's follow-latest pill (centre), this bubble, and the app's
   quick-capture FAB — which is `position: fixed` over the same right corner.
   Sideways is already taken, so the bubble stacks ABOVE the FAB instead: it
   is the only one of the four that lives in the scroll flow and can. The
   3rem is the FAB's own height (`h-12`); its offset from the edge is 1rem on a
   phone and 1.5rem above that, and the breakpoint is the dock's own — the
   whole contest only exists while the dock is sticky. */
@media (max-width: 1199px) {
  .followup-bubble-row { padding-bottom: calc(3rem + 1.5rem + 10px); }
}
@media (max-width: 767px) {
  .followup-bubble-row { padding-bottom: calc(3rem + 1rem + 10px); }
}

/* Resting affordance, not a bar: it holds one corner and grows its label on
   hover, so reading the transcript is never traded against reaching it. */
.followup-bubble {
  position: relative;
  display: inline-flex;
  justify-content: flex-start;
  align-items: center;
  height: 44px;
  width: auto;
  padding: 0 12px;
  border: 0;
  border-radius: 9999px;
  background: var(--color-blue-700);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: -0.006em;
  overflow: hidden;
  box-shadow: 0 2px 4px rgba(15, 23, 42, 0.06), 0 14px 30px -12px rgba(29, 78, 216, 0.62);
  transition: padding 240ms cubic-bezier(0.32, 0.72, 0, 1), box-shadow 240ms ease;
}

.followup-bubble:hover,
.followup-bubble.is-open {
  background: var(--color-blue-700);
  color: #fff;
  padding-right: 18px;
  box-shadow: 0 2px 4px rgba(15, 23, 42, 0.08), 0 18px 36px -12px rgba(29, 78, 216, 0.72);
}

.followup-bubble:focus-visible {
  outline: 2px solid var(--color-blue-500);
  outline-offset: 2px;
}

.followup-bubble-gloss {
  position: absolute;
  inset: 0;
  border-radius: 9999px;
  background: linear-gradient(160deg, rgba(255, 255, 255, 0.22), rgba(255, 255, 255, 0) 55%);
  pointer-events: none;
}

.followup-bubble-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 0;
  opacity: 0;
  margin-left: 0;
  white-space: nowrap;
  overflow: hidden;
  transition: max-width 260ms cubic-bezier(0.32, 0.72, 0, 1), opacity 180ms ease,
    margin-left 260ms cubic-bezier(0.32, 0.72, 0, 1);
}

.followup-bubble.is-open .followup-bubble-label {
  max-width: 150px;
  opacity: 1;
  margin-left: 9px;
}

/* Swaps with the label: the dot says "live" while collapsed, and gets out of
   the way once the words say it. */
.followup-bubble-dot {
  position: absolute;
  top: 9px;
  right: 9px;
  width: 8px;
  height: 8px;
  border-radius: 9999px;
  background: var(--color-emerald-400);
  box-shadow: 0 0 0 2px var(--color-blue-700);
  opacity: 1;
  transition: opacity 180ms ease;
  pointer-events: none;
}

.followup-bubble.is-open .followup-bubble-dot { opacity: 0; }

@media (prefers-reduced-motion: reduce) {
  .followup-bubble,
  .followup-bubble-label,
  .followup-bubble-dot { transition: none; }
}

/* The action row's constraint is THIS box's width, never the viewport's: at
   768px the panel is only ~400px wide (it shares the feed column with the TOC
   rail), so a viewport breakpoint tuned on a phone leaves it broken in the
   middle — Send wrapped to a second line with a 56px status beside it. One
   container, one honest condition, instead of three viewport proxies for it. */
.followup-panel {
  container-type: inline-size;
  container-name: composer;
  border: 1px solid var(--color-primary);
  border-radius: 16px;
  background: var(--color-surface);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 18px 36px -14px rgba(15, 23, 42, 0.26);
  overflow: hidden;
}

.followup-dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 9999px;
  background: var(--color-success);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-success) 16%, transparent);
}

.followup-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.3125rem;
  white-space: nowrap;
  padding: 0.25rem 0.5625rem;
  border-radius: 8px;
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  font-size: 11px;
  font-weight: 600;
  min-width: 0;
  max-width: 9rem;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Six controls against ~318px of panel. `flex-wrap` above is the safety net —
   a long model name drops Send to a second line instead of painting it over
   the run controls — but the common case should not need it, so a narrow panel
   buys the 14px it is short by from the gaps and the model chip. */
@container composer (max-width: 520px) {
  .followup-actions { gap: 5px; }
  .followup-chip { max-width: 4.5rem; }
}

/* Narrower than this the chip's own cap is down to ~45px, which shows "c…" and
   nothing else — a control's worth of width spent on no information. The model
   is on the panel's meta line and in the session header; the row's five
   working controls are not anywhere else. */
@container composer (max-width: 340px) {
  .followup-chip { display: none; }
}

/* The resting line is a keyboard hint, and the run controls beside it are the
   only way to stop an agent from this bar. On a phone there is room for one of
   them and no ⌘⏎ to press, so the hint is what goes — truncated to an ellipsis
   it reads as a collision with Stop rather than as information. */
/* Basis 0, so a long server sentence truncates into whatever the controls
   leave rather than wrapping the row — at 1280 its natural 632px pushed the
   status onto a second line and Send onto a third, with room to spare. */
.followup-status {
  flex: 1 1 0;
  min-width: 0;
  text-align: right;
}

@container composer (max-width: 640px) {
  /* The resting line is a keyboard hint, and a panel this narrow has room for
     the run controls or for it, not both — so it is what goes. There is no ⌘⏎
     to press on the phone this width usually means, either. */
  .followup-hint { display: none; }
  /* Below this the controls leave the status nothing to truncate INTO, and a
     refusal is the one message the operator has to read. `order`, not DOM
     order: it takes a line of its own BELOW the controls, not above them. */
  .followup-status {
    flex-basis: 100%;
    order: 1;
    text-align: left;
  }
}

.followup-field { position: relative; }

/* Field and mirror wrap identically or the highlight slides off the token it
   marks, so every metric that drives wrapping is declared for the pair at
   once — never on one of them alone. */
.followup-ta,
.followup-hl {
  padding: 0.5rem 0.875rem 0.375rem;
  font-size: 13.5px;
  line-height: 1.5;
}

/* The panel supplies the frame; the field inside it is chromeless. */
.followup-ta {
  position: relative;
  z-index: 1;
  display: block;
  border: 0;
  border-radius: 0;
  background: transparent;
  resize: none;
}

.followup-ta:focus, .followup-ta:focus-visible { box-shadow: none; }

@media (prefers-reduced-motion: reduce) {
  .followup-pill { transition: none; }
}
</style>
