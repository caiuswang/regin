<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import Button from '../ui/Button.vue'
import { useBreakpoint } from '../../composables/useBreakpoint'
import { useNotificationCenter } from '../../composables/useNotificationCenter'

// Tier 1. The agent is stopped until this is dealt with, so it does not
// auto-dismiss and "Later" only snoozes. On a phone the same state renders as
// a bottom sheet you can swipe away — same promise, thumb-reachable.
const {
  blocker, bannerVisible, stripVisible, blockerWaitedFor, snoozeSeconds,
  snoozeBlocker, reopenBlocker,
} = useNotificationCenter()

const router = useRouter()
// Matches the shell's own `max-width: 767px` mobile branch in AppLayout.
const { isMdUp } = useBreakpoint()
const isMobile = computed(() => !isMdUp.value)

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

function answerInLive() {
  const traceId = blocker.value?.trace_id
  router.push(traceId ? `/live?session=${encodeURIComponent(traceId)}` : '/live')
}

function later() {
  dragY.value = 0
  dragging.value = false
  // A swipe that snoozes unmounts the sheet before the click task runs, so
  // `handleClick` never gets to clear the flag — and a stale `true` would eat
  // the next real activation (an Enter on the handle after it re-opens).
  swallowClick = false
  snoozeBlocker()
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
  // click on pointerup, which would snooze the sheet the user just kept.
  swallowClick = moved > TAP_SLOP_PX
  if (dragY.value > DISMISS_AFTER_PX) later()
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
  later()
}
</script>

<template>
  <!-- Collapsed: snoozed but still waiting. One line, one action. -->
  <div v-if="stripVisible" class="blocker-strip" role="alert">
    <span class="blocker-dot blocker-dot-blink" aria-hidden="true" />
    <span class="blocker-strip-label">1 decision waiting</span>
    <span class="blocker-meta">agent paused · {{ blockerWaitedFor }}</span>
    <Button size="sm" variant="danger" class="ml-auto" @click="reopenBlocker">Answer</Button>
  </div>

  <!-- Full, mobile: a bottom sheet over a scrim, draggable down to snooze. -->
  <div
    v-else-if="bannerVisible && isMobile"
    class="blocker-scrim cursor-pointer hover:brightness-110 focus-visible:outline-2"
    role="alertdialog"
    aria-label="Agent paused, awaiting your decision"
    @click.self="later"
  >
    <div class="blocker-sheet" :style="sheetStyle">
      <!-- Only the handle drags. A whole-sheet drag target would swallow
           text selection and taps on the buttons below it. -->
      <Button
        variant="ghost"
        size="sm"
        class="blocker-grabber"
        aria-label="Dismiss for now — the session stays paused"
        @click="handleClick"
        @pointerdown="dragStart"
        @pointermove="dragMove"
        @pointerup="dragEnd"
        @pointercancel="dragCancel"
      >
        <span class="blocker-grabber-bar" aria-hidden="true" />
      </Button>
      <div class="blocker-head">
        <span class="blocker-dot blocker-dot-pulse" aria-hidden="true" />
        <span class="inbox-pill inbox-pill-red">Blocker</span>
        <span class="blocker-title">Agent paused</span>
        <span class="blocker-meta ml-auto">{{ blockerWaitedFor }}</span>
      </div>
      <div class="blocker-question">{{ blocker.question || blocker.title }}</div>
      <div v-if="blocker.session_title" class="blocker-session">↳ {{ blocker.session_title }}</div>
      <ul v-if="blocker.options.length" class="blocker-options">
        <li v-for="option in blocker.options" :key="option" class="blocker-option">{{ option }}</li>
      </ul>
      <Button size="lg" variant="primary" class="w-full" @click="answerInLive">
        Answer in live session
      </Button>
      <p class="blocker-foot">
        Swipe down to leave it — the session stays paused and flagged, and the
        sheet returns in {{ snoozeSeconds }}s
      </p>
    </div>
  </div>

  <!-- Full, desktop: a sticky banner above the page content. -->
  <div v-else-if="bannerVisible" class="blocker-banner" role="alert">
    <div class="blocker-head">
      <span class="blocker-dot blocker-dot-pulse" aria-hidden="true" />
      <span class="inbox-pill inbox-pill-red">Blocker</span>
      <span class="blocker-title">Agent paused · awaiting your decision</span>
      <span class="blocker-meta">waiting {{ blockerWaitedFor }}</span>
      <Button size="sm" variant="ghost" class="ml-auto blocker-later" @click="later">
        Later — dismiss &times;
      </Button>
    </div>

    <div class="blocker-question">{{ blocker.question || blocker.title }}</div>
    <div v-if="blocker.session_title" class="blocker-session">↳ {{ blocker.session_title }}</div>

    <!-- The options the agent offered, verbatim. They are shown, not wired:
         the answer is given in the session, so a button here would only look
         like it had done something. -->
    <ul v-if="blocker.options.length" class="blocker-options">
      <li v-for="option in blocker.options" :key="option" class="blocker-option">{{ option }}</li>
    </ul>

    <!-- No "Mark read" here on purpose. Reading is not answering: the card
         would go read while the agent stayed parked, and the banner would
         (correctly) refuse to close — a button that visibly does nothing. -->
    <div class="flex items-center gap-2 flex-wrap">
      <Button size="md" variant="primary" @click="answerInLive">Answer in live session</Button>
      <span class="blocker-foot ml-auto">
        Dismissing leaves the session paused — it returns in {{ snoozeSeconds }}s
        and stays flagged in the list.
      </span>
    </div>
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

.blocker-banner {
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
  margin-bottom: 1rem;
  padding: 0.85rem 1rem;
  border: 1.5px solid var(--color-red-300);
  background: var(--color-warning-soft);
  border-radius: 0.75rem;
  box-shadow: 0 2px 6px rgb(15 23 42 / 6%);
}

.blocker-strip {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 1rem;
  padding: 0.55rem 0.85rem;
  border: 1px solid var(--color-red-300);
  background: var(--color-danger-soft);
  border-radius: 0.625rem;
}

.blocker-strip-label {
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--color-danger);
}

.blocker-head {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-wrap: wrap;
}

.blocker-title {
  font-size: 0.9rem;
  font-weight: 700;
  letter-spacing: -0.012em;
  color: var(--color-fg);
}

.blocker-meta {
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

.blocker-options {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.blocker-option {
  font-size: 0.82rem;
  color: var(--color-fg);
  background: var(--color-surface);
  border: 1px solid var(--color-amber-300);
  border-radius: 0.5rem;
  padding: 0.45rem 0.7rem;
}

.blocker-foot {
  font-size: 0.7rem;
  color: var(--color-fg-muted);
  margin: 0;
}

.blocker-later { color: var(--color-warning-strong); }

.blocker-dot {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 50%;
  background: var(--color-danger);
}

.blocker-dot-pulse { animation: blocker-pulse 1.8s ease-out infinite; }
.blocker-dot-blink { animation: blocker-blink 1.1s steps(1, end) infinite; }

@keyframes blocker-pulse {
  0% { box-shadow: 0 0 0 0 rgb(220 38 38 / 50%); }
  70% { box-shadow: 0 0 0 8px rgb(220 38 38 / 0%); }
  100% { box-shadow: 0 0 0 0 rgb(220 38 38 / 0%); }
}

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
}

@keyframes sheet-up {
  from { opacity: 0; transform: translateY(100%); }
  to { opacity: 1; transform: none; }
}

.blocker-sheet { cursor: default; }

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

  .blocker-dot-pulse,
  .blocker-dot-blink { animation: none; }
}
</style>
