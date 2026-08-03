<script setup>
// The banner's header row. Carries the pager, which is the whole reason this
// is a component and not markup: several agents park at once, and the previous
// banner kept a single slot — so agent 4's question silently replaced agents
// 1-3's, with no sign the others were ever there.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import { useNotificationCenter } from '../../composables/useNotificationCenter'

defineProps({
  title: { type: String, default: 'Agent paused · awaiting your decision' },
  showClock: { type: Boolean, default: true },
})

const {
  blockerCount, blockerPos, blockerWaitedFor, nextBlocker, prevBlocker,
} = useNotificationCenter()

const paged = computed(() => blockerCount.value > 1)
const position = computed(() => `Decision ${blockerPos.value + 1} of ${blockerCount.value}`)
</script>

<template>
  <div class="blocker-head">
    <span class="blocker-dot blocker-dot-pulse" aria-hidden="true" />
    <span class="inbox-pill inbox-pill-red">Blocker</span>
    <span class="blocker-title">{{ title }}</span>

    <!-- aria-live so a screen reader hears which decision it moved to; the
         visible label is the same string, so there is nothing extra to keep
         in step. -->
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

    <span v-if="showClock" class="blocker-meta">waiting {{ blockerWaitedFor }}</span>
    <slot />
  </div>
</template>

<style scoped>
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

.blocker-pager {
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

/* Defined here, not in the parent: scoped styles do not reach into an
   extracted child, so the dot would render unstyled if these stayed with the
   banner that used to own this markup. */
.blocker-dot {
  width: 8px;
  height: 8px;
  flex: none;
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
  .blocker-dot-pulse { animation: none; }
}
</style>
