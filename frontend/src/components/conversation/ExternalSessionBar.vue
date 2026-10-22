<script setup>
// The fourth state under a transcript: a session another app owns and regin
// can only record (WorkBuddy's desktop app, codex). Whether it is still
// running is that app's to know, so the copy claims neither "running" nor
// "over", and there is no Resume — the launch route has no channel for it.
import Icon from '../ui/Icon.vue'

defineProps({
  app: { type: String, required: true },
})
</script>

<template>
  <div class="external-bar" data-testid="external-session-bar">
    <Icon name="message-square" :size="16" class="shrink-0 opacity-70" />
    <span>Recorded from {{ app }}. regin can't continue this conversation — open it in {{ app }} to keep going.</span>
  </div>
</template>

<style scoped>
.external-bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.625rem 0.875rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
  font-size: 0.8125rem;
  color: var(--color-fg-muted);
}
</style>
