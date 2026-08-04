<script setup>
// Tier 1. The agent is stopped until this is dealt with, so nothing here
// auto-closes. The banner is now only the FLAG that a decision is waiting —
// one line, key facts, three controls. The decision itself (question, context,
// options) opens in BlockerPopout, because rendering it inline pushed the page
// down by however much the agent happened to write.
//
// Two distinct outs, deliberately separate controls: FOLD collapses to the
// strip (still counted, reopen any time), DISMISS retires this decision for
// good (server-backed — it never comes back). Neither claims the agent
// resumed; only a delivered answer may do that.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import BlockerHead from './BlockerHead.vue'
import BlockerPopout from './BlockerPopout.vue'
import { useBreakpoint } from '../../composables/useBreakpoint'
import { useNotificationCenter } from '../../composables/useNotificationCenter'

const {
  blocker, blockerCount, blockerPos, bannerVisible, stripVisible, popoutVisible,
  blockerWaitedFor, foldBlocker, dismissBlocker, openDecision,
} = useNotificationCenter()

// Matches the shell's own `max-width: 767px` mobile branch in AppLayout.
const { isMdUp } = useBreakpoint()
const isMobile = computed(() => !isMdUp.value)

const stripLabel = computed(() => (blockerCount.value === 1
  ? '1 decision waiting'
  : `${blockerCount.value} decisions waiting`))

const mobileMeta = computed(() => {
  const waiting = `waiting ${blockerWaitedFor.value}`
  if (blockerCount.value < 2) return waiting
  return `${waiting} · Decision ${blockerPos.value + 1} of ${blockerCount.value}`
})

function dismissCurrent() {
  dismissBlocker(blocker.value)
}
</script>

<template>
  <!-- Collapsed: folded but still waiting. One line, one action. -->
  <div v-if="stripVisible" class="blocker-strip" role="alert">
    <span class="blocker-dot blocker-dot-blink" aria-hidden="true" />
    <span class="blocker-strip-label">{{ stripLabel }}</span>
    <span class="blocker-strip-meta">agent paused · {{ blockerWaitedFor }}</span>
    <Button size="sm" variant="danger" class="ml-auto" @click="openDecision">Answer</Button>
  </div>

  <!-- Full, mobile: a slim bar pinned to the bottom of the viewport. The
       decision opens as a sheet on top of it, not in place of it. -->
  <div
    v-else-if="bannerVisible && isMobile"
    class="blocker-bar"
    role="alert"
    :inert="popoutVisible || undefined"
  >
    <span class="blocker-dot blocker-dot-blink" aria-hidden="true" />
    <span class="blocker-bar-text">
      <span class="blocker-bar-title">{{ blocker.title || 'Agent paused' }}</span>
      <span class="blocker-bar-meta">{{ mobileMeta }}</span>
    </span>
    <Button size="sm" variant="danger" class="blocker-bar-btn" @click="openDecision">
      Answer
    </Button>
    <Button size="sm" variant="ghost" class="blocker-bar-btn blocker-later" @click="foldBlocker">
      Fold
    </Button>
  </div>

  <!-- Full, desktop: a sticky one-line banner above the page content.
       `inert` while the pop-out is open: the modal claims to be modal, but the
       banner behind it stayed in the tab order — and tabbing out of the dialog
       reached "Dismiss ×", which retires the decision for good. -->
  <div
    v-else-if="bannerVisible"
    class="blocker-banner"
    role="alert"
    :inert="popoutVisible || undefined"
  >
    <BlockerHead>
      <Button size="sm" variant="danger" @click="openDecision">Answer ⤢</Button>
      <Button size="sm" variant="ghost" class="blocker-later" @click="foldBlocker">
        Fold
      </Button>
      <!-- A card derived straight from the parked state has no inbox row to
           dismiss against — the park itself is the fact, so there is no
           "don't show again" to promise. -->
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
  </div>

  <!-- Teleported: the scrim carries a backdrop-filter, and any transformed or
       filtered ancestor inside the scroller would make `position: fixed`
       resolve against that ancestor instead of the viewport. -->
  <Teleport to="body">
    <BlockerPopout v-if="popoutVisible" />
  </Teleport>
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
  margin-bottom: 1rem;
  padding: 0.7rem 1rem;
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

/* Mobile bar ---------------------------------------------------------- */
.blocker-bar {
  position: fixed;
  inset-inline: 0;
  bottom: 0;
  /* Above the topics comments-drawer backdrop (60), which otherwise laid a
     40%-opacity scrim over the Answer/Fold buttons of a STOPPED agent and made
     them unclickable. Still below the pop-out's 90. */
  z-index: 61;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.5rem 0.85rem calc(0.5rem + env(safe-area-inset-bottom));
  background: var(--color-surface);
  border-top: 2px solid var(--color-danger);
  box-shadow: 0 -6px 20px rgb(15 23 42 / 14%);
  animation: bar-up 0.28s cubic-bezier(0.2, 0.9, 0.3, 1) both;
}

@keyframes bar-up {
  from { opacity: 0; transform: translateY(100%); }
  to { opacity: 1; transform: none; }
}

.blocker-bar-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}

.blocker-bar-title {
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--color-fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.blocker-bar-meta {
  font-size: 0.7rem;
  color: var(--color-fg-muted);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Thumb targets, not chrome: 44pt is the floor for anything on a phone. */
.blocker-bar-btn {
  flex: none;
  min-height: 44px;
  min-width: 44px;
  padding-inline: 0.75rem;
}

.blocker-later { color: var(--color-warning-strong); }

/* Quieter than Fold: dismiss is the rarer, sharper action (it never comes
   back), so it must not read as the default way out. */
.blocker-dismiss { color: var(--color-fg-muted); }

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

@media (prefers-reduced-motion: reduce) {
  .blocker-banner,
  .blocker-strip,
  .blocker-bar { animation: none; }

  .blocker-dot-blink { animation: none; }
}
</style>
