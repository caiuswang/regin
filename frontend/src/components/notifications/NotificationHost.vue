<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import NotificationToast from './NotificationToast.vue'
import { useBreakpoint } from '../../composables/useBreakpoint'
import { useNotificationCenter } from '../../composables/useNotificationCenter'

// Tier 2. Where the stack lives: bottom-right on desktop (the top of the
// content pane belongs to the tier-1 banner), one sheet under the status bar
// on a phone (a column of cards there would bury the page).
// `fold`, not `drop`, for ✕ and Open: neither marks the message read, so it
// stays counted in the badge and in the "N more folded" pill. `drop` is for
// messages that are no longer unread.
const { toasts, folded, fold, markRead, onOpen } = useNotificationCenter()
const router = useRouter()
// Matches the shell's own `max-width: 767px` mobile branch in AppLayout.
const { isMdUp } = useBreakpoint()
const isMobile = computed(() => !isMdUp.value)

// A phone shows one at a time; the rest are already counted in `folded`.
const visible = computed(() => (isMobile.value ? toasts.value.slice(0, 1) : toasts.value))

function open(message) {
  fold(message.id)
  router.push(message.trace_id ? `/trace/sessions/${message.trace_id}` : '/inbox')
}

// Clicking the OS notification lands on the same place as "Open in trace".
onOpen(open)
</script>

<template>
  <div class="toast-host" :class="{ 'toast-host-mobile': isMobile }" aria-live="polite">
    <div v-if="folded && !isMobile" class="toast-folded">
      <span class="toast-folded-dot" aria-hidden="true" />
      <span>{{ folded }} more folded into the Inbox badge</span>
    </div>

    <NotificationToast
      v-for="message in visible"
      :key="message.id"
      :message="message"
      @open="open"
      @mark-read="markRead"
      @dismiss="fold"
    />
  </div>
</template>

<style scoped>
/* Bottom-right on desktop, not top-right: the tier-1 banner occupies the top
   of the content pane, and a stack overlapping it hides the very control that
   dismisses it. Toasts are also the lower tier — they should not sit on top of
   the thing that stopped the agent. */
.toast-host {
  position: fixed;
  right: 1.25rem;
  bottom: 1.25rem;
  z-index: 55;
  width: min(24.5rem, calc(100vw - 2.5rem));
  max-height: calc(100vh - 2.5rem);
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.625rem;
  overflow: hidden;
  /* The stack is a floating layer over the page: only the cards inside it
     take clicks, never the empty column between them. */
  pointer-events: none;
}

/* On a phone the toast is a sheet under the status bar, where a notification
   is expected to come from — and where a thumb is not about to tap it. */
.toast-host-mobile {
  right: 0.75rem;
  left: 0.75rem;
  top: 0.75rem;
  bottom: auto;
  width: auto;
}

.toast-folded {
  pointer-events: auto;
  align-self: flex-end;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.35rem 0.75rem;
  border-radius: 999px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  box-shadow: 0 2px 6px rgb(15 23 42 / 6%);
  font-size: 0.72rem;
  color: var(--color-fg-muted);
}

.toast-folded-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-primary);
}
</style>
