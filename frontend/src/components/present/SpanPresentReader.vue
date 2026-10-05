<script setup>
// Presentation mode: one span's complete payload, rendered as a document.
//
// The feed answers *what happened*; the 340px detail rail can only excerpt.
// This answers *what the span actually said*. It is a reading surface, not a
// data source — nothing is fetched, filtered, or transformed beyond formatting.
//
// Built on Reka's dialog primitives, not on ui/Dialog.vue: the geometry here is
// three fixed bands over the whole viewport rather than a centred card, but the
// primitive still supplies focus trap, scroll lock, Escape and aria-modal.
import { computed, ref, watch, nextTick } from 'vue'
import { DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle } from 'reka-ui'
import PresentContent from './PresentContent.vue'
import { presentFor } from '../../utils/presentModel.js'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import FollowUpComposer from '../conversation/FollowUpComposer.vue'
import { useCopy } from '../../composables/useCopy.js'
import { openFilePreview } from '../../composables/useFilePreview.js'
import { readSpanSource } from '../files/source.js'

const props = defineProps({
  span: { type: Object, default: null },
  // { id, label } of the neighbours in spanSeq order, or null at the ends.
  prev: { type: Object, default: null },
  next: { type: Object, default: null },
  // Position within the READABLE walk, and its size. 0 when the open span is
  // off that list (opened directly while "content only" filters it out).
  position: { type: Number, default: 0 },
  total: { type: Number, default: 0 },
  onlyBody: { type: Boolean, default: true },
  skipped: { type: Number, default: 0 },
  offList: { type: Boolean, default: false },
  turnLabel: { type: String, default: '' },
  children: { type: Array, default: () => [] },
  // Bumped only on a deliberate open/step — see usePresentReader. The scroll
  // reset keys on this, not on span_id.
  navSeq: { type: Number, default: 0 },
  // The ⌘K palette marks the matched term in its preview; the reader you land
  // in after clicking a hit gets the same mark, or a 5k-word response opens at
  // the top with no sign of where the hit was.
  highlight: { type: String, default: '' },
  // Reply is the same bridge send the conversation feed offers, addressed from
  // the span you are reading; it exists only where a follow-up can land.
  // The SAME bundle the feed gets — taken whole, so the docked composer cannot
  // be handed a narrower context than the feed's copy of itself.
  followUp: { type: Object, default: () => ({}) },
  sessionId: { type: String, default: '' },
  replyTurn: { type: Number, default: 0 },
})
const emit = defineEmits(['close', 'move', 'open-child', 'toggle-only-body', 'sent'])

const open = computed({
  get: () => !!props.span,
  set: (v) => { if (!v) emit('close') },
})

const vm = computed(() => (props.span
  ? presentFor(props.span, { turnLabel: props.turnLabel, children: props.children })
  : null))

const { copyText, isCopied } = useCopy()

const readSource = computed(() => readSpanSource(props.span))

// Display preference, not a content switch: `wide` buys diff and shell room
// (1320px panel) while the prose measure stays capped independently, so lines
// never grow past a readable length.
const WIDE_KEY = 'regin.present.wide'
const wide = ref(readWide())
function readWide() {
  try { return localStorage.getItem(WIDE_KEY) === '1' } catch { return false }
}
function toggleWide() {
  wide.value = !wide.value
  try { localStorage.setItem(WIDE_KEY, wide.value ? '1' : '0') } catch { /* private mode */ }
}

const bodyEl = ref(null)
const panelEl = ref(null)
// Focus the BODY rather than a control or the panel. The reader opens at the
// top of a document, so the caret belongs on the document and ↑/↓ mean "walk
// spans" from the first press — but the body is also the only scrollable
// element here, and the panel above it is `overflow: hidden`. Focusing the
// panel left PageUp/PageDown/Space/Home/End with no scrollable ancestor to
// act on while ↑/↓ were claimed by the walk, so a keyboard-only reader could
// not get past the first screenful of any span (WCAG 2.1.1).
function focusPanel() {
  nextTick(() => { (bodyEl.value || panelEl.value?.$el)?.focus?.() })
}
// A deliberate step resets the reader's scroll: landing mid-document in a span
// you have not read yet is disorienting in a way the feed never is. Keyed on
// `navSeq`, NOT on span_id — the live poll rebinds a retired placeholder to
// its resolved twin under a new id, and that is the same document.
watch(() => props.navSeq, () => {
  nextTick(() => { if (bodyEl.value) bodyEl.value.scrollTop = 0 })
})

// `entry.label` is the raw `spanLabel`, which is unbounded — a `tool.failure`
// carries its whole error. The visible span is `truncate`d, but the aria-label
// is not, so a screen reader announced a 16k-character button name.
const STEP_LABEL_MAX = 70
function stepLabel(entry) {
  const raw = String(entry?.label || '').replace(/\s+/g, ' ').trim()
  return raw.length > STEP_LABEL_MAX ? `${raw.slice(0, STEP_LABEL_MAX)}…` : raw
}

const canReply = computed(() => !!props.followUp.canFollowUp)
const replyOpen = ref(false)
// A subject line, not a title: the composer is addressed from this span, and
// the reader is the only place the follow-up has one.
const replyContext = computed(() => (vm.value ? `about ${vm.value.name}` : ''))
function toggleReply() { replyOpen.value = !replyOpen.value }

// The composer swallows its own Escape, but only while the caret is in it.
// Preventing the primitive's close covers both cases with one rule: the first
// Escape retires the composer, the second retires the reader.
// The full-text search palette stacks OVER the reader (⌘K works from in
// here). It teleports to <body>, so to this dialog it is "outside": without
// these exemptions the focus trap yanks the caret straight back out of the
// palette's input, and Escape retires the reader instead of the palette.
// Probed through the DOM rather than by importing the search state — the
// reader should not have to know what is stacked above it, only that
// something is.
// The file preview drawer stacks the same way, and needs the same three.
const STACKED_ABOVE = '[data-search-palette], [data-file-preview]'
const stackedOpen = () => !!document.querySelector(STACKED_ABOVE)
const inStacked = el => !!el?.closest?.(STACKED_ABOVE)

function onEscapeKeyDown(e) {
  if (stackedOpen()) { e.preventDefault(); return }
  if (!replyOpen.value) return
  e.preventDefault()
  replyOpen.value = false
}

// The composer's autocomplete list is teleported to <body>, so from the
// dialog's point of view accepting a row is a click outside itself. The
// search palette is outside for the same reason.
function onInteractOutside(e) {
  const target = e.detail?.originalEvent?.target
  if (target?.closest?.('.live-cmd-menu') || inStacked(target)) e.preventDefault()
}

// Focus leaving for the palette is legitimate — let it go, or the trap
// returns the caret and the palette cannot be typed into.
function onFocusOutside(e) {
  if (stackedOpen()) e.preventDefault()
}

const TYPING_TAGS = ['INPUT', 'TEXTAREA', 'SELECT']
function isTypingTarget(el) {
  return !!el && (TYPING_TAGS.includes(el.tagName) || el.isContentEditable)
}

const MOVE_KEYS = new Map([
  ['ArrowDown', 1], ['ArrowRight', 1], ['j', 1],
  ['ArrowUp', -1], ['ArrowLeft', -1], ['k', -1],
])

// `grab`, the dev overlay that copies an element's source context, arms on
// Ctrl+Shift+G and listens on `window` — so the reader was the one surface its
// own debug tool could not reach. The exemption is that chord and no other: a
// blanket "modifier chords are nobody's shortcut" is false here.
// The search palette also binds Ctrl/Cmd+K on `window`, but in the CAPTURE
// phase, so it is reached before this handler regardless; what it additionally
// needs is the focus-trap and Escape exemptions above.
function isGrabChord(e) {
  return e.ctrlKey && e.shiftKey && (e.key === 'g' || e.key === 'G')
}

function onKeydown(e) {
  const k = e.key
  // The reader owns the viewport, so it owns the keyboard: everything but the
  // two keys the dialog primitive needs (Escape to dismiss, Tab to cycle the
  // focus trap) — and the one chord above — stops here rather than bubbling to
  // the document listeners that drive the page underneath: F, H, T, \, /, G
  // would otherwise mutate a view the reader is covering.
  if (k !== 'Escape' && k !== 'Tab' && !isGrabChord(e)) e.stopPropagation()
  // Typed text is the field's, never a shortcut — but it still stops above, or
  // it reaches the page's own listeners underneath.
  if (e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target)) return
  const delta = MOVE_KEYS.get(k)
  if (delta) {
    e.preventDefault(); emit('move', delta)
  } else if (canReply.value && (k === 'c' || k === 'C')) {
    e.preventDefault(); toggleReply()
  }
}
</script>

<template>
  <DialogRoot v-model:open="open">
    <DialogPortal>
      <DialogOverlay class="present-backdrop" />
      <DialogContent
        ref="panelEl"
        class="present-panel"
        :class="wide ? 'is-wide' : ''"
        :aria-describedby="undefined"
        data-testid="span-present-reader"
        data-grab-reveal=".present-open"
        tabindex="-1"
        @open-auto-focus.prevent="focusPanel"
        @keydown="onKeydown"
        @escape-key-down="onEscapeKeyDown"
        @interact-outside="onInteractOutside"
        @focus-outside="onFocusOutside"
      >
        <header v-if="vm" class="present-header">
          <div class="present-identity">
            <span class="present-dot" :class="vm.dot" aria-hidden="true"></span>
            <span class="present-kind">{{ vm.kindLabel }}</span>
            <span class="present-name">{{ vm.name }}</span>
            <span v-if="vm.turnLabel" class="present-turn">{{ vm.turnLabel }}</span>
            <Button
              variant="ghost"
              size="sm"
              class="present-ctl ml-auto"
              :aria-label="wide ? 'Use comfortable width' : 'Use wide width'"
              :title="wide ? 'Comfortable width' : 'Wide width'"
              @click="toggleWide"
            >{{ wide ? '⇥' : '⇤' }}</Button>
            <Button
              v-if="canReply"
              variant="ghost"
              size="sm"
              class="present-ctl present-reply-btn"
              :class="replyOpen ? 'is-on' : ''"
              :aria-pressed="replyOpen"
              aria-label="Reply about this span"
              title="Reply about this span (c)"
              data-testid="present-reply"
              @click="toggleReply"
            ><Icon name="message-square" :size="14" />Reply</Button>
            <Button
              v-if="readSource"
              variant="ghost"
              size="sm"
              class="present-ctl"
              aria-label="View the content this Read captured"
              title="View in the file viewer"
              data-testid="present-read-view"
              @click="openFilePreview(readSource)"
            ><Icon name="file" :size="14" />View</Button>
            <Button
              variant="ghost"
              size="sm"
              class="present-ctl"
              aria-label="Copy span content"
              title="Copy span content"
              @click="copyText(vm.copyText)"
            >{{ isCopied(vm.copyText) ? 'Copied' : '⧉ Copy' }}</Button>
            <Button
              variant="ghost"
              size="sm"
              class="present-ctl"
              aria-label="Close reader"
              title="Close (Esc)"
              @click="emit('close')"
            >✕</Button>
          </div>
          <DialogTitle class="present-title">{{ vm.title }}</DialogTitle>
          <div class="present-vitals">
            <span v-for="chip in vm.chips" :key="chip.k" class="present-vital">
              <span class="present-vital-k">{{ chip.k }}</span>
              <span class="present-vital-v">{{ chip.v }}</span>
            </span>
            <span v-if="vm.readMeta" class="present-vital-v">{{ vm.readMeta }}</span>
          </div>
        </header>

        <!-- `tabindex="0"`: a scrollable region must be reachable by keyboard,
             and this is the element the browser's own scroll keys act on. -->
        <div
          v-if="vm"
          ref="bodyEl"
          class="present-body"
          data-present-body
          tabindex="0"
          role="region"
          :aria-label="vm.title"
        >
          <div class="present-col">
            <PresentContent
              :vm="vm"
              :trace-id="sessionId"
              :only-body="onlyBody"
              :highlight="highlight"
              @open-child="emit('open-child', $event)"
            />
          </div>
          <aside v-if="vm.hasAttrs" class="present-meta">
            <div class="present-block-label">Attributes</div>
            <dl class="present-attrs">
              <template v-for="attr in vm.attrs" :key="attr.k">
                <dt>{{ attr.k }}</dt>
                <dd>{{ attr.v }}</dd>
              </template>
            </dl>
          </aside>
        </div>

        <!-- Mounted for the whole life of the reader, not behind the toggle:
             the composer owns the draft, and a v-if would discard typed text
             on every dismiss and on every span step. -->
        <!-- `|| replyOpen` is what keeps an in-flight draft: a session that
             ends mid-read flips canReply false, and unmounting there would
             destroy typed text. The send then fails visibly, text preserved. -->
        <div
          v-if="vm && (canReply || replyOpen)"
          class="present-reply"
          :class="replyOpen ? 'is-open' : ''"
        >
          <FollowUpComposer
            v-model:open="replyOpen"
            :session-id="sessionId"
            :turn-number="replyTurn"
            :context="followUp"
            :bubble="false"
            :context-label="replyContext"
            placeholder="Ask about this span, or send the next instruction…"
            @sent="emit('sent')"
          />
        </div>

        <footer v-if="vm" class="present-footer">
          <!-- The label ellipsizes inside a span, never on the Button itself:
               `truncate` on the primitive fights its own flex centring. -->
          <Button
            variant="ghost"
            size="sm"
            class="present-step justify-start"
            :disabled="!prev"
            :aria-label="prev ? `Previous span: ${stepLabel(prev)}` : 'Start of trace'"
            @click="emit('move', -1)"
          ><span class="min-w-0 flex-1 truncate text-left">↑ {{ prev ? stepLabel(prev) : 'start of trace' }}</span></Button>
          <span class="present-pos">
            <!-- Off the readable list — opened directly, which the filter
                 never blocks — the counter says so instead of claiming a
                 position the span does not have. -->
            <span v-if="position >= 1" class="present-count">{{ position }} / {{ total }}</span>
            <span v-else-if="offList" class="present-count">off-list · {{ total }} readable</span>
            <Button
              variant="ghost"
              size="sm"
              class="present-filter"
              :class="onlyBody ? 'is-on' : ''"
              :aria-pressed="onlyBody"
              :title="onlyBody
                ? 'Only spans with a body; ↑↓ skips the rest'
                : '↑↓ walks every span in the trace'"
              @click="emit('toggle-only-body')"
            >{{ onlyBody ? '◉' : '○' }} content only<span
              v-if="onlyBody && skipped"
              class="present-filter-skipped"
            >· {{ skipped }} skipped</span></Button>
            <span class="present-legend">↑↓ span · <span
              v-if="canReply"
              class="present-legend-key"
            >C reply</span><span v-if="canReply"> · </span>esc close</span>
          </span>
          <Button
            variant="ghost"
            size="sm"
            class="present-step ml-auto justify-end"
            :disabled="!next"
            :aria-label="next ? `Next span: ${stepLabel(next)}` : 'End of trace'"
            @click="emit('move', 1)"
          ><span class="min-w-0 flex-1 truncate text-right">{{ next ? stepLabel(next) : 'end of trace' }} ↓</span></Button>
        </footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<!-- NOT scoped: Reka portals this content to <body>, beyond scoped data-v. -->
<style>
.present-backdrop {
  position: fixed; inset: 0; z-index: var(--z-modal);
  background: rgba(var(--scrim-rgb), 0.58); backdrop-filter: blur(3px);
}
.present-panel {
  position: fixed; z-index: var(--z-modal);
  top: 24px; bottom: 24px; left: 50%; transform: translateX(-50%);
  width: calc(100vw - 48px); max-width: 1040px;
  display: flex; flex-direction: column;
  background: var(--color-surface); color: var(--color-fg);
  border-radius: 20px; overflow: hidden;
  box-shadow: 0 40px 90px -20px rgba(var(--scrim-rgb), 0.55);
}
.present-panel.is-wide { max-width: 1320px; }
.present-panel:focus { outline: none; }
/* The body is tabbable only so the browser's scroll keys have somewhere to act
   (see `focusPanel`); it is a document, not a control, and the reader focuses
   it itself on open. A ring on it therefore never marks a focus the reader
   moved — `:focus-visible` would light it on the first ↑/↓ of the span walk,
   which moves no focus at all, and frame the whole reading band from then on. */
.present-body:focus, .present-body:focus-visible { outline: none; }

.present-header {
  flex-shrink: 0; padding: 18px 26px 14px;
  border-bottom: 1px solid var(--color-border); background: var(--color-surface);
}
.present-identity { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.present-dot { width: 9px; height: 9px; border-radius: 9999px; flex-shrink: 0; }
.present-kind {
  font-size: 11px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--color-fg-subtle);
}
.present-name {
  font-family: var(--font-mono); font-size: 11px; color: var(--color-fg-muted);
  background: var(--color-surface-2); padding: 2px 6px; border-radius: 5px; word-break: break-all;
}
.present-turn { font-family: var(--font-mono); font-size: 11px; color: var(--color-fg-faint); }
.present-ctl { font-family: var(--font-mono); font-size: 11.5px; }
/* Hard ceiling, not just a model-side cap. The header is `flex-shrink: 0`
   above a `flex: 1` document, so any title tall enough to matter steals the
   whole reading band and pushes the ↑↓ footer off the viewport. The model caps
   the STRING; this caps the BOX, so a CJK or zero-space title cannot get past
   both. */
.present-title {
  margin: 10px 0 0; font-size: 20px; font-weight: 700; letter-spacing: -0.015em;
  line-height: 1.3; color: var(--color-fg); text-wrap: pretty; overflow-wrap: anywhere;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3;
  overflow: hidden;
}
.present-vitals {
  margin-top: 6px; display: flex; flex-wrap: wrap; gap: 12px;
  font-family: var(--font-mono); font-size: 11.5px;
}
.present-vital-k { color: var(--color-fg-faint); margin-right: 4px; }
.present-vital-v { color: var(--color-fg-muted); font-weight: 600; }

/* One card surface across all four bands, not the page canvas: the code and
   terminal wells inside are `--code-bg`, which under dark inversion sits a
   hair off the canvas and dissolves into it. Against the surface they recess. */
.present-body {
  flex: 1; min-height: 0; overflow-y: auto;
  scroll-padding-top: 26px;
  background: var(--color-surface);
  display: grid; grid-template-columns: minmax(0, 1fr); gap: 28px; padding: 26px 26px 40px;
}
.present-col { max-width: 74ch; min-width: 0; }
.present-panel.is-wide .present-col { max-width: 92ch; }
.present-meta { min-width: 0; }
.present-attrs { margin: 0; font-size: 11.5px; }
.present-attrs dt {
  font-family: var(--font-mono); color: var(--color-fg-faint); margin-top: 9px; word-break: break-all;
}
.present-attrs dd {
  margin: 2px 0 0; font-family: var(--font-mono); color: var(--color-fg-muted);
  white-space: pre-wrap; overflow-wrap: anywhere; max-height: 14rem; overflow-y: auto;
}
@media (min-width: 860px) {
  .present-body { grid-template-columns: minmax(0, 1fr) 236px; }
  /* `top: 26px` matches the body's own top padding: at `top: 0` the pinned
     attributes column sat flush against the header border while the content
     column beside it kept its inset, and the two visibly de-aligned. */
  .present-meta { position: sticky; top: 26px; align-self: start; }
}

.present-ctl.present-reply-btn { display: inline-flex; align-items: center; gap: 6px; }
.present-reply-btn.is-on {
  color: var(--color-info);
  background: var(--color-info-soft);
  box-shadow: inset 0 0 0 1px var(--color-info);
}

/* Its own band between the document and the footer — the reader's three fixed
   bands become four, so the composer never floats over the text it is about.
   Collapsed it contributes no box at all. */
.present-reply {
  flex-shrink: 0;
  background: var(--color-surface);
}
.present-reply.is-open {
  padding: 14px 26px;
  border-top: 1px solid var(--color-border);
  max-height: 46vh;
  overflow-y: auto;
}

/* Reka's modal layer parks `pointer-events: none` on <body> so nothing behind
   the dialog is clickable — and the composer's autocomplete is teleported
   there, so it inherits it and renders rows that cannot be clicked. Hand the
   list its events back; @interact-outside keeps the click from dismissing the
   reader that the list belongs to. */
body:has(.present-panel) .live-cmd-menu { pointer-events: auto; }

.present-footer {
  flex-shrink: 0; display: flex; align-items: center; gap: 12px;
  padding: 10px 26px; border-top: 1px solid var(--color-border);
  background: var(--color-surface);
}
.present-step { max-width: 38%; min-width: 0; font-size: 11.5px; }
.present-step:disabled { color: var(--color-border-strong); }
/* `min-width: 0` so the centre group is the one that gives. Its children are
   `nowrap`, so its `min-width: auto` floor was its max-content width and the
   whole deficit landed on the two step buttons — the prev/next span NAMES, the
   only content those buttons have, collapsed to four characters while the
   legend they were making room for stayed. */
.present-pos {
  margin: 0 auto; min-width: 0; display: flex; align-items: center; gap: 10px;
  font-family: var(--font-mono); font-size: 11px; white-space: nowrap;
  overflow: hidden;
}
.present-count { flex-shrink: 0; color: var(--color-fg-muted); font-weight: 600; }
.present-filter {
  flex-shrink: 0;
  font-family: var(--font-mono); font-size: 11px; gap: 5px;
  color: var(--color-fg-faint);
}
.present-filter.is-on { color: var(--color-info); background: var(--color-info-soft); }
.present-filter-skipped { color: var(--color-fg-faint); }
/* The group shrinks below its `nowrap` content by design (see above); the key
   legend is the one part cheap enough to lose, so it absorbs the deficit and
   ellipsizes instead of painting over the next-span button. */
.present-legend {
  min-width: 0; overflow: hidden; text-overflow: ellipsis;
  color: var(--color-fg-faint);
}
.present-legend-key { color: var(--color-info); font-weight: 600; }

/* Narrow viewport: full-bleed, and the meta column falls below the content
   (the grid is already single-column under 860px). */
@media (max-width: 720px) {
  .present-panel {
    top: 0; bottom: 0; left: 0; transform: none;
    width: 100vw; max-width: none; border-radius: 0;
  }
  .present-body { padding: 18px 16px 32px; }
  .present-header, .present-footer { padding-left: 16px; padding-right: 16px; }
  .present-reply.is-open { padding-left: 16px; padding-right: 16px; }
}

/* The legend is the footer's most droppable content, so it goes FIRST — at
   721px the panel is at its narrowest inset width and still had it, while
   ≤720px (where the panel goes full-bleed and gains 48px) is where it was
   being dropped. */
@media (max-width: 900px) {
  .present-legend { display: none; }
}
</style>
