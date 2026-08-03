<script setup>
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import { inboxTypeMeta } from '../../constants/inboxTypes'
import { useNotificationPrefs } from '../../composables/useNotificationPrefs'

const props = defineProps({
  message: { type: Object, required: true },
})
const emit = defineEmits(['open', 'mark-read', 'dismiss'])

const { prefs } = useNotificationPrefs()

const meta = computed(() => inboxTypeMeta(props.message.msg_type))
const time = computed(() => {
  const stamp = props.message.created_at
  if (!stamp) return ''
  const parsed = new Date(stamp)
  return Number.isNaN(parsed.getTime())
    ? ''
    : parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
})
</script>

<template>
  <div class="toast" role="status">
    <span class="toast-rail" :class="meta.dot" aria-hidden="true" />
    <div class="toast-body">
      <div class="flex items-center gap-2">
        <span class="inbox-pill" :class="meta.pill">{{ meta.label }}</span>
        <span class="toast-time">{{ time }}</span>
        <Button
          variant="ghost"
          size="icon"
          class="ml-auto -mr-1 h-7 w-7"
          aria-label="Dismiss notification"
          @click="emit('dismiss', message.id)"
        >&times;</Button>
      </div>

      <div class="toast-title">{{ message.title || message.body }}</div>
      <div v-if="message.title && message.body" class="toast-text">{{ message.body }}</div>
      <div v-if="message.session_title" class="toast-session">↳ {{ message.session_title }}</div>

      <div class="flex items-center gap-2 pt-0.5">
        <Button size="sm" variant="primary" @click="emit('open', message)">Open in trace</Button>
        <Button size="sm" variant="secondary" @click="emit('mark-read', message.id)">Mark read</Button>
        <span class="toast-tier">stays {{ prefs.toastDurationSec }}s · then badge</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.toast {
  pointer-events: auto;
  display: flex;
  overflow: hidden;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 0.75rem;
  box-shadow: 0 12px 30px rgb(15 23 42 / 13%);
  animation: toast-in 0.3s cubic-bezier(0.2, 0.9, 0.3, 1) both;
}

@keyframes toast-in {
  from { opacity: 0; transform: translateY(14px) scale(0.97); }
  to { opacity: 1; transform: none; }
}

@media (prefers-reduced-motion: reduce) {
  .toast { animation: none; }
}

.toast-rail { width: 4px; flex: none; }

.toast-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.75rem 0.8rem 0.7rem 0.7rem;
}

.toast-time {
  font-size: 0.7rem;
  color: var(--color-fg-faint);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.toast-title {
  font-size: 0.875rem;
  font-weight: 700;
  letter-spacing: -0.012em;
  line-height: 1.35;
  color: var(--color-fg);
}

.toast-text {
  font-size: 0.78rem;
  color: var(--color-fg-muted);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.toast-session,
.toast-tier {
  font-size: 0.68rem;
  color: var(--color-fg-faint);
}

.toast-session {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.toast-tier { margin-left: auto; }
</style>
