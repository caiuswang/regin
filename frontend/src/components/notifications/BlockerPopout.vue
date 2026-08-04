<script setup>
// The decision itself, lifted out of the banner into a modal — the same
// detail/pop-out shape the inbox uses, for the same reason: the body is
// arbitrarily long, and a surface pinned above the page cannot grow with it.
//
// Selection and delivery are two steps here, unlike the old inline chips. The
// banner's one-tap answer was reachable from every page in the app, so a
// mis-click answered a stopped agent with no undo; `Send` is the confirmation.
// That only applies to the `question` shape — `decision` (allow/deny) already
// has its own stage→confirm gate and the read-only shape has nothing to send,
// so both keep delegating to BlockerActions rather than growing a second path.
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import Button from '../ui/Button.vue'
import BlockerActions from './BlockerActions.vue'
import { useBreakpoint } from '../../composables/useBreakpoint'
import { useNotificationCenter } from '../../composables/useNotificationCenter'
import { useSheetDrag } from '../../composables/useSheetDrag'

const router = useRouter()
const {
  blocker, blockerCount, blockerPos, blockerWaitedFor, blockerChoice,
  nextBlocker, prevBlocker, chooseOption, closePopout, foldBlocker,
  dismissBlocker, answerBlocker,
} = useNotificationCenter()

const phase = ref('ready')
const detail = ref('')

// Matches the shell's own `max-width: 767px` mobile branch in AppLayout.
const { isMdUp } = useBreakpoint()
const isMobile = computed(() => !isMdUp.value)
// On a phone the sheet's swipe-down is a FOLD, not a close: the decision is
// still waiting, and a gesture that silently discarded it would be the one
// escape route that loses a stopped agent.
const drag = useSheetDrag(foldBlocker)

const paged = computed(() => blockerCount.value > 1)
const position = computed(() => `Decision ${blockerPos.value + 1} of ${blockerCount.value}`)
const canAnswer = computed(() => blocker.value?.answerable === 'question')
const sending = computed(() => phase.value === 'sending')
const question = computed(() => blocker.value?.question || blocker.value?.title || '')
// `header` is AskUserQuestion's own one-line framing of the ask, and
// tool_name/requested_permission is what names the gated call. Both already
// ride the card — the pop-out reads them, it does not need new server fields.
const context = computed(() => blocker.value?.header || '')
const agent = computed(
  () => blocker.value?.tool_name || blocker.value?.requested_permission || '')
const meta = computed(() => [
  blocker.value?.session_title, agent.value, `waiting ${blockerWaitedFor.value}`,
].filter(Boolean).join(' · '))

const options = computed(() => (blocker.value?.options || []).map((option, at) => ({
  ...option,
  at,
  // regin's own AskUserQuestion convention marks the suggested answer in the
  // label text, so the pill is derived rather than a field the server has to
  // start sending.
  rec: /\(recommended\)/i.test(option.label || ''),
})))

function openLive() {
  const traceId = blocker.value?.trace_id
  router.push(traceId ? `/live/${encodeURIComponent(traceId)}` : '/live')
}

async function send() {
  const at = blockerChoice.value
  const row = blocker.value
  // The disabled button is the visible half of this; the guard is the half
  // that survives an Enter on a focused-but-disabled control and a stale click.
  if (at === null || !row || sending.value) return
  const option = (row.options || [])[at]
  if (!option) return
  phase.value = 'sending'
  detail.value = ''
  const res = await answerBlocker(row, option)
  // Delivered means the card is already retired and this component is
  // unmounting; anything else leaves the agent parked, so the surface must
  // stay up and say why rather than close on a promise it cannot keep.
  if (res.delivered) return
  phase.value = 'failed'
  detail.value = res.detail || 'send failed'
}

// The pager swaps the card under a mounted pop-out, so anything describing the
// PREVIOUS send has to go with it — a stale "Not delivered" left under the next
// agent's question is an error message attributed to the wrong decision, and a
// stranded `sending` would disable that card's radios and its Send button.
// Keyed on the CARD, never on the row object. `refreshBlockers` replaces every
// row wholesale on a 60s reconcile and on each route change, so watching object
// identity fired on refreshes that changed nothing: it erased a live
// "Not delivered" while the agent was still parked, and — worse — cleared
// `sending` mid-flight, re-enabling Send and dropping the re-entrancy guard so
// a second click could post `bridge-answer` twice to one parked agent.
const cardId = computed(() => (blocker.value
  ? `${blocker.value.trace_id}::${blocker.value.msg_key}::${blocker.value.version || 1}`
  : ''))

watch(cardId, () => {
  phase.value = 'ready'
  detail.value = ''
})

// Roving tabindex: a radiogroup is ONE tab stop, and arrows move within it.
// Without this every option was its own stop and the arrow keys — the first
// thing a screen-reader user tries in a radiogroup — did nothing.
const groupEl = ref(null)

function moveChoice(delta) {
  const total = options.value.length
  if (!total) return
  const from = blockerChoice.value === null ? 0 : blockerChoice.value + delta
  const next = ((from % total) + total) % total
  chooseOption(blocker.value, next)
  nextTick(() => groupEl.value?.querySelectorAll('[role="radio"]')[next]?.focus())
}

function onKeydown(event) {
  if (event.key === 'Escape') closePopout()
}

// Focus moves in on open and back to whatever opened it on close. Without the
// restore, closing with Esc dropped focus to <body> and the keyboard user had
// to tab the whole page to get back to the Answer button they just left.
const cardEl = ref(null)
let returnFocusTo = null

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  returnFocusTo = document.activeElement
  nextTick(() => {
    const first = groupEl.value?.querySelector('[role="radio"]')
    ;(first || cardEl.value)?.focus?.()
  })
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  // Only if it is still in the document: after a delivered answer the banner
  // unmounts in the same patch, and focusing a detached node is a silent no-op
  // that drops focus to <body> — the very thing the restore exists to avoid.
  if (returnFocusTo?.isConnected) returnFocusTo.focus?.()
})
</script>

<template>
  <!-- Clicking the backdrop is the standard "leave it for now" gesture, so it
       is a real target and says so: pointer cursor, and it darkens on hover. -->
  <div
    class="blocker-popout-scrim cursor-pointer hover:brightness-110 focus-visible:outline-2"
    role="dialog"
    aria-modal="true"
    aria-labelledby="blocker-popout-heading"
    @click.self="closePopout"
  >
    <div
      ref="cardEl"
      class="blocker-popout"
      tabindex="-1"
      :style="isMobile ? drag.sheetStyle.value : undefined"
    >
      <!-- Only the handle drags. A whole-sheet drag target would swallow text
           selection and taps on the options below it. -->
      <Button
        v-if="isMobile"
        variant="ghost"
        size="sm"
        class="blocker-grabber"
        aria-label="Fold into the strip — the session stays paused"
        @click="drag.handleClick"
        @pointerdown="drag.dragStart"
        @pointermove="drag.dragMove"
        @pointerup="drag.dragEnd"
        @pointercancel="drag.dragCancel"
      >
        <span class="blocker-grabber-bar" aria-hidden="true" />
      </Button>

      <div class="blocker-popout-head">
        <span class="blocker-popout-tile" aria-hidden="true">
          <span class="blocker-dot blocker-dot-pulse" />
        </span>
        <div class="blocker-popout-headings">
          <div id="blocker-popout-heading" class="blocker-popout-title">Decision required</div>
          <div class="blocker-popout-meta" :title="meta">{{ meta }}</div>
        </div>

        <div v-if="paged" class="blocker-pager" role="group" aria-label="Waiting decisions">
          <Button
            size="sm"
            variant="ghost"
            class="blocker-pager-btn"
            aria-label="Previous decision"
            @click="prevBlocker"
          >‹</Button>
          <span class="blocker-pager-label" aria-live="polite">{{ position }}</span>
          <Button
            size="sm"
            variant="ghost"
            class="blocker-pager-btn"
            aria-label="Next decision"
            @click="nextBlocker"
          >›</Button>
        </div>

        <Button
          size="sm"
          variant="ghost"
          class="blocker-popout-close"
          aria-label="Close without answering"
          @click="closePopout"
        >&times;</Button>
      </div>

      <div class="blocker-popout-body">
        <div class="blocker-popout-question">{{ question }}</div>
        <p v-if="context" class="blocker-popout-context">{{ context }}</p>

        <!-- Answerable question: the span's options, verbatim and in its order.
             Radios, not buttons — picking is not yet sending. -->
        <div
          v-if="canAnswer"
          ref="groupEl"
          class="blocker-popout-options"
          role="radiogroup"
          aria-label="Answer options"
        >
          <Button
            v-for="option in options"
            :key="option.index"
            variant="ghost"
            size="sm"
            role="radio"
            :aria-checked="blockerChoice === option.at"
            :tabindex="option.at === (blockerChoice ?? 0) ? 0 : -1"
            :disabled="sending"
            @keydown.down.prevent="moveChoice(1)"
            @keydown.right.prevent="moveChoice(1)"
            @keydown.up.prevent="moveChoice(-1)"
            @keydown.left.prevent="moveChoice(-1)"
            :class="[
              'blocker-popout-option h-auto w-full items-start justify-start',
              'whitespace-normal px-3 py-2.5 text-start hover:bg-transparent',
              { 'blocker-popout-option-on': blockerChoice === option.at },
            ]"
            @click="chooseOption(blocker, option.at)"
          >
            <span class="blocker-popout-radio" aria-hidden="true" />
            <span class="blocker-popout-option-text">
              <span class="blocker-popout-option-label">
                {{ option.label }}
                <span v-if="option.rec" class="blocker-popout-rec">Recommended</span>
              </span>
              <span v-if="option.description" class="blocker-popout-option-desc">
                {{ option.description }}
              </span>
            </span>
          </Button>
        </div>

        <!-- allow/deny, or a card no channel reaches. Unchanged surfaces. -->
        <!-- Keyed by card: it holds its own `staged`/`phase`, and paging the
             queue — or a re-prompt superseding in place — under a live
             component would carry a half-staged allow/deny onto a different
             question. Same key as the selection map, version included. -->
        <BlockerActions v-else :key="cardId" :row="blocker" :show-live="false" />

        <p v-if="phase === 'failed'" class="blocker-popout-failed" role="status">
          Not delivered — {{ detail }}. The agent is still paused; open the live
          session to answer there.
        </p>
      </div>

      <div class="blocker-popout-foot">
        <Button
          v-if="canAnswer"
          size="sm"
          variant="danger"
          :disabled="blockerChoice === null || sending"
          @click="send"
        >
          {{ sending ? 'Sending…' : 'Send decision → resume agent' }}
        </Button>
        <Button size="sm" variant="ghost" class="blocker-later" @click="foldBlocker">
          Fold
        </Button>
        <Button
          v-if="blocker?.id"
          size="sm"
          variant="ghost"
          class="blocker-dismiss"
          @click="dismissBlocker(blocker)"
        >
          Dismiss &times;
        </Button>
        <Button size="sm" variant="ghost" class="blocker-popout-live" @click="openLive">
          Open live session →
        </Button>
      </div>

      <p class="blocker-popout-rule">
        Fold keeps it in the strip until you reopen it · Dismiss removes this
        decision for good · Esc closes without answering. The session stays
        paused and flagged either way.
      </p>
    </div>
  </div>
</template>

<style scoped>
.blocker-popout-scrim {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  background: rgb(15 23 42 / 46%);
  backdrop-filter: blur(5px);
}

.blocker-popout {
  width: 660px;
  max-width: 100%;
  max-height: 84vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 1.125rem;
  box-shadow: 0 12px 30px rgb(15 23 42 / 13%);
  /* Deliberately NO fill mode. `both` keeps the keyframes' `transform: none`
     applied forever after the entry animation ends, and an animation
     declaration outranks the style attribute — which silently pinned the
     phone sheet's drag transform at zero, so it never followed the finger. */
  animation: popout-in 0.22s cubic-bezier(0.3, 1.05, 0.4, 1);
}

@keyframes popout-in {
  from { opacity: 0; transform: translateY(8px) scale(0.99); }
  to { opacity: 1; transform: none; }
}

/* Sticky so the queue pager and the close stay reachable in a long ask — the
   body is the agent's prose and has no length the card can rely on. */
.blocker-popout-head {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 1rem 1.125rem 0.8rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: 1.125rem 1.125rem 0 0;
}

.blocker-popout-tile {
  flex: none;
  width: 28px;
  height: 28px;
  border-radius: 0.5625rem;
  background: var(--color-danger-soft);
  display: grid;
  place-items: center;
}

.blocker-popout-headings {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}

.blocker-popout-title {
  font-size: 0.875rem;
  font-weight: 700;
  letter-spacing: -0.012em;
  color: var(--color-fg);
}

.blocker-popout-meta {
  font-size: 0.7rem;
  color: var(--color-fg-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.blocker-popout-close {
  flex: none;
  min-width: 1.75rem;
  height: 1.75rem;
  padding: 0;
  font-size: 1rem;
  line-height: 1;
  color: var(--color-fg-muted);
}

.blocker-popout-body {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
  padding: 1.0625rem 1.25rem 1.25rem;
}

.blocker-popout-question {
  font-size: 1rem;
  font-weight: 700;
  letter-spacing: -0.014em;
  line-height: 1.45;
  color: var(--color-fg);
  white-space: pre-line;
}

.blocker-popout-context {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-fg-muted);
  line-height: 1.6;
}

.blocker-popout-options {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

/* Scoped under the group so these beat the ghost variant's own hover/border
   utilities, which land at the same specificity as a bare class here. */
.blocker-popout-options .blocker-popout-option {
  gap: 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 0.625rem;
  background: var(--color-surface);
  color: var(--color-fg);
}

.blocker-popout-options .blocker-popout-option:hover:not(:disabled) {
  border-color: var(--color-amber-500);
}

.blocker-popout-options .blocker-popout-option-on,
.blocker-popout-options .blocker-popout-option-on:hover {
  border-color: var(--color-amber-500);
  background: var(--color-amber-50);
}

.blocker-popout-radio {
  flex: none;
  margin-top: 0.2rem;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid var(--color-border-strong);
}

.blocker-popout-option-on .blocker-popout-radio {
  border-color: var(--color-amber-500);
  background:
    radial-gradient(circle, var(--color-amber-500) 0 3.5px, transparent 3.5px);
}

.blocker-popout-option-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.blocker-popout-option-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-fg);
}

.blocker-popout-rec {
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-warning-strong);
  background: var(--color-amber-50);
  border: 1px solid var(--color-amber-300);
  border-radius: 999px;
  padding: 0.05rem 0.4rem;
}

.blocker-popout-option-desc {
  font-size: 0.75rem;
  line-height: 1.5;
  color: var(--color-fg-muted);
}

.blocker-popout-failed {
  margin: 0;
  font-size: 0.75rem;
  color: var(--color-danger);
}

.blocker-popout-foot {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  padding: 0 1.25rem;
}

.blocker-later { color: var(--color-warning-strong); }

/* Quieter than Fold: dismiss is the rarer, sharper action (it never comes
   back), so it must not read as the default way out. */
.blocker-dismiss { color: var(--color-fg-muted); }

.blocker-popout-live {
  margin-inline-start: auto;
  color: var(--color-fg-muted);
}

.blocker-popout-rule {
  margin: 0;
  padding: 0.75rem 1.25rem 1.125rem;
  font-size: 0.7rem;
  line-height: 1.6;
  color: var(--color-fg-muted);
}

.blocker-pager {
  flex: none;
  display: flex;
  align-items: center;
  gap: 0.15rem;
  padding: 0.1rem 0.15rem;
  border: 1px solid var(--color-red-200);
  border-radius: 999px;
  background: var(--color-surface);
}

.blocker-pager-btn {
  min-width: 1.5rem;
  height: 1.5rem;
  padding: 0 0.3rem;
  color: var(--color-danger);
  font-size: 0.95rem;
  line-height: 1;
}

.blocker-pager-label {
  font-size: 0.72rem;
  font-weight: 700;
  color: var(--color-danger);
  white-space: nowrap;
  padding: 0 0.15rem;
}

.blocker-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-danger);
}

.blocker-dot-pulse { animation: blocker-pulse 1.8s ease-out infinite; }

@keyframes blocker-pulse {
  0% { box-shadow: 0 0 0 0 rgb(220 38 38 / 50%); }
  70% { box-shadow: 0 0 0 8px rgb(220 38 38 / 0%); }
  100% { box-shadow: 0 0 0 0 rgb(220 38 38 / 0%); }
}

@media (prefers-reduced-motion: reduce) {
  .blocker-popout { animation: none; }
  .blocker-dot-pulse { animation: none; }
}

/* Phone: the modal becomes the bottom sheet it replaced, so the swipe-to-fold
   gesture still has something to grab and the actions stay in thumb reach. */
@media (max-width: 767px) {
  .blocker-popout-scrim {
    align-items: flex-end;
    padding: 0;
  }

  .blocker-popout {
    width: 100%;
    max-height: 88vh;
    border-radius: 1.375rem 1.375rem 0 0;
    border-top: 2px solid var(--color-danger);
  }

  .blocker-popout-head {
    border-radius: 0;
    padding-top: 0.4rem;
  }

  .blocker-popout-rule { padding-bottom: calc(1.125rem + env(safe-area-inset-bottom)); }
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
</style>
