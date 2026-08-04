<script setup>
// Tier 1. The agent is stopped until this is dealt with, so nothing here
// auto-closes. Two distinct outs, deliberately separate controls: FOLD
// collapses the detail into the strip (still counted, reopen any time),
// DISMISS retires this decision for good (server-backed — it never comes
// back). On a phone the same state renders as a bottom sheet whose swipe-down
// is the fold.
//
// The question and its options come off the parked SPAN (server-assembled in
// lib/agent_messages/blockers.py), not off the card's prose, which is why the
// options here are real controls: the index sent back is the one the bridge
// selects. See BlockerActions for the three answerable shapes.
import { computed, ref } from 'vue'
import Button from '../ui/Button.vue'
import BlockerActions from './BlockerActions.vue'
import BlockerHead from './BlockerHead.vue'
import { useBreakpoint } from '../../composables/useBreakpoint'
import { useNotificationCenter } from '../../composables/useNotificationCenter'

const {
  blocker, blockerCount, bannerVisible, stripVisible, blockerWaitedFor,
  foldBlocker, unfoldBlocker, dismissBlocker,
} = useNotificationCenter()

// Matches the shell's own `max-width: 767px` mobile branch in AppLayout.
const { isMdUp } = useBreakpoint()
const isMobile = computed(() => !isMdUp.value)

const stripLabel = computed(() => (blockerCount.value === 1
  ? '1 decision waiting'
  : `${blockerCount.value} decisions waiting`))

const dragY = ref(0)
const dragging = ref(false)
let dragFrom = 0
let moved = 0
let swallowClick = false

const DISMISS_AFTER_PX = 90
const TAP_SLOP_PX = 6

const sheetStyle = computed(() => ({
  transform: `translateY(${dragY.value}px)`,
  transition: dragging.value ? 'none' : 'transform 0.26s cubic-bezier(0.2, 0.9, 0.3, 1)',
}))

function foldAway() {
  dragY.value = 0
  dragging.value = false
  // A swipe that folds unmounts the sheet before the click task runs, so
  // `handleClick` never gets to clear the flag — and a stale `true` would eat
  // the next real activation (an Enter on the handle after it re-opens).
  swallowClick = false
  foldBlocker()
}

function dismissCurrent() {
  dismissBlocker(blocker.value)
}

function dragStart(event) {
  dragFrom = event.clientY
  moved = 0
  dragging.value = true
  event.currentTarget.setPointerCapture?.(event.pointerId)
}

function dragMove(event) {
  if (!dragging.value) return
  const delta = event.clientY - dragFrom
  moved = Math.max(moved, Math.abs(delta))
  dragY.value = Math.max(0, delta)
}

function dragEnd() {
  if (!dragging.value) return
  // A drag that snapped back said "keep it" — but the browser still fires a
  // click on pointerup, which would fold the sheet the user just kept.
  swallowClick = moved > TAP_SLOP_PX
  if (dragY.value > DISMISS_AFTER_PX) foldAway()
  else {
    dragging.value = false
    dragY.value = 0
  }
}

// A cancelled gesture fires no click, so the flag must not survive it and eat
// the next real tap (or an Enter on the focused handle).
function dragCancel() {
  dragEnd()
  swallowClick = false
}

function handleClick() {
  if (swallowClick) {
    swallowClick = false
    return
  }
  foldAway()
}
</script>

<template>
  <!-- Collapsed: folded but still waiting. One line, one action. -->
  <div v-if="stripVisible" class="blocker-strip" role="alert">
    <span class="blocker-dot blocker-dot-blink" aria-hidden="true" />
    <span class="blocker-strip-label">{{ stripLabel }}</span>
    <span class="blocker-strip-meta">agent paused · {{ blockerWaitedFor }}</span>
    <Button size="sm" variant="danger" class="ml-auto" @click="unfoldBlocker">Answer</Button>
  </div>

  <!-- Full, mobile: a bottom sheet over a scrim, draggable down to fold. -->
  <div
    v-else-if="bannerVisible && isMobile"
    class="blocker-scrim cursor-pointer hover:brightness-110 focus-visible:outline-2"
    role="alertdialog"
    aria-label="Agent paused, awaiting your decision"
    @click.self="foldAway"
  >
    <div class="blocker-sheet" :style="sheetStyle">
      <!-- Only the handle drags. A whole-sheet drag target would swallow
           text selection and taps on the buttons below it. -->
      <Button
        variant="ghost"
        size="sm"
        class="blocker-grabber"
        aria-label="Fold into the strip — the session stays paused"
        @click="handleClick"
        @pointerdown="dragStart"
        @pointermove="dragMove"
        @pointerup="dragEnd"
        @pointercancel="dragCancel"
      >
        <span class="blocker-grabber-bar" aria-hidden="true" />
      </Button>
      <BlockerHead title="Agent paused" />
      <div class="blocker-question">{{ blocker.question || blocker.title }}</div>
      <div v-if="blocker.session_title" class="blocker-session">↳ {{ blocker.session_title }}</div>
      <BlockerActions :row="blocker" compact />
      <!-- A card derived straight from the parked state has no inbox row to
           dismiss against — the park itself is the fact, so there is no
           "don't show again" to promise. -->
      <div v-if="blocker.id" class="blocker-outs">
        <Button size="sm" variant="ghost" class="blocker-dismiss" @click="dismissCurrent">
          Dismiss — don't show again
        </Button>
      </div>
      <p class="blocker-foot">
        Swipe down to fold it into the strip — the session stays paused and
        flagged either way
      </p>
    </div>
  </div>

  <!-- Full, desktop: a sticky banner above the page content. -->
  <div v-else-if="bannerVisible" class="blocker-banner" role="alert">
    <BlockerHead>
      <Button size="sm" variant="ghost" class="ml-auto blocker-later" @click="foldBlocker">
        Fold
      </Button>
      <Button
        v-if="blocker.id"
        size="sm"
        variant="ghost"
        class="blocker-dismiss"
        @click="dismissCurrent"
      >
        Dismiss &times;
      </Button>
    </BlockerHead>

    <div class="blocker-question">{{ blocker.question || blocker.title }}</div>
    <div v-if="blocker.session_title" class="blocker-session">↳ {{ blocker.session_title }}</div>

    <BlockerActions :row="blocker" />

    <!-- No "Mark read" here on purpose. Reading is not answering: the card
         would go read while the agent stayed parked, and the banner would
         (correctly) refuse to close — a button that visibly does nothing. -->
    <p class="blocker-foot blocker-foot-end">
      Fold keeps it in the strip; Dismiss removes this decision for good.
      The session stays paused and flagged either way.
    </p>
  </div>
</template>

<style scoped>
.blocker-banner,
.blocker-strip {
  animation: blocker-in 0.28s ease both;
}

@keyframes blocker-in {
  from { opacity: 0; transform: translateY(-12px); }
  to { opacity: 1; transform: none; }
}

/* amber-50, not amber-100. The card is a full-width wash behind body text, and
   at 100 the yellow read as the loudest thing on the page — louder than the
   question it was carrying. The alert lives in the border, the dot and the
   pill; the fill only has to separate the card from the page. */
.blocker-banner {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  margin-bottom: 1rem;
  padding: 0.85rem 1rem;
  border: 1px solid var(--color-red-200);
  background: var(--color-amber-50);
  border-radius: 0.75rem;
  box-shadow: 0 1px 3px rgb(15 23 42 / 5%);
}

.blocker-strip {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 1rem;
  padding: 0.55rem 0.85rem;
  border: 1px solid var(--color-red-200);
  background: var(--color-danger-soft);
  border-radius: 0.625rem;
}

.blocker-strip-label {
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--color-danger);
}

.blocker-strip-meta {
  font-size: 0.75rem;
  color: var(--color-fg-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.blocker-question {
  font-size: 0.95rem;
  font-weight: 700;
  letter-spacing: -0.012em;
  color: var(--color-fg);
  line-height: 1.45;
  white-space: pre-line;
}

.blocker-session {
  font-size: 0.72rem;
  color: var(--color-fg-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.blocker-foot {
  font-size: 0.7rem;
  color: var(--color-fg-muted);
  margin: 0;
}

.blocker-foot-end { align-self: flex-end; text-align: end; }

.blocker-later { color: var(--color-warning-strong); }

/* Quieter than Fold: dismiss is the rarer, sharper action (it never comes
   back), so it must not read as the default way out. */
.blocker-dismiss { color: var(--color-fg-muted); }

.blocker-outs { display: flex; justify-content: flex-end; }

.blocker-dot {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 50%;
  background: var(--color-danger);
}

.blocker-dot-blink { animation: blocker-blink 1.1s steps(1, end) infinite; }

@keyframes blocker-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.15; }
}

/* Scrim + sheet (mobile) --------------------------------------------- */
/* Tapping the scrim is the standard "leave it for now" gesture, so it is a
   real target: pointer cursor, and it darkens on hover to say so. */
.blocker-scrim {
  position: fixed;
  inset: 0;
  z-index: 60;
  background: rgb(15 23 42 / 42%);
  display: flex;
  align-items: flex-end;
  cursor: pointer;
  transition: background 0.15s ease;
}

.blocker-scrim:hover { background: rgb(15 23 42 / 52%); }

.blocker-scrim:focus-visible {
  outline: 2px solid var(--color-ring);
  outline-offset: -2px;
}

.blocker-sheet {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
  padding: 0.6rem 1rem 1.6rem;
  background: var(--color-surface);
  border-top: 2px solid var(--color-danger);
  border-radius: 1.375rem 1.375rem 0 0;
  box-shadow: 0 -14px 40px rgb(15 23 42 / 28%);
  touch-action: none;
  animation: sheet-up 0.32s cubic-bezier(0.2, 0.9, 0.3, 1) both;
  cursor: default;
}

@keyframes sheet-up {
  from { opacity: 0; transform: translateY(100%); }
  to { opacity: 1; transform: none; }
}

.blocker-grabber {
  flex: none;
  align-self: center;
  width: 72px;
  height: 1.75rem;
  padding: 0;
  touch-action: none;
}

.blocker-grabber-bar {
  display: block;
  width: 44px;
  height: 5px;
  border-radius: 3px;
  background: var(--color-border-strong);
}

@media (prefers-reduced-motion: reduce) {
  .blocker-banner,
  .blocker-strip,
  .blocker-sheet { animation: none; }

  .blocker-dot-blink { animation: none; }
}
</style>
