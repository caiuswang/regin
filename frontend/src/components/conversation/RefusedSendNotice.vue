<script setup>
import { RouterLink } from 'vue-router'
import Button from '../ui/Button.vue'

defineProps({
  refused: { type: Object, required: true },
})
const emit = defineEmits(['post', 'resume'])
</script>

<template>
  <div
    v-if="refused.refusal.value || refused.posted.value"
    class="refused-send"
    role="status"
    data-testid="refused-send"
  >
    <template v-if="refused.refusal.value">
      <p class="refused-send-detail" data-testid="refused-send-detail">{{ refused.refusal.value.detail }}</p>
      <div class="flex flex-wrap items-center gap-2">
        <Button
          v-if="refused.refusal.value.resume?.session_id"
          variant="primary"
          size="sm"
          :disabled="refused.resuming.value || refused.posting.value"
          data-testid="refused-send-resume"
          @click="emit('resume')"
        >{{ refused.resuming.value ? 'Resuming…' : 'Resume session & send' }}</Button>
        <Button
          variant="secondary"
          size="sm"
          :disabled="refused.posting.value || refused.resuming.value"
          data-testid="refused-send-post-comment"
          @click="emit('post')"
        >{{ refused.posting.value ? 'Posting…' : 'Post as task comment' }}</Button>
        <span
          v-if="refused.postError.value"
          class="text-[11px] text-red-600"
          role="alert"
          data-testid="refused-send-error"
        >✗ {{ refused.postError.value }}</span>
      </div>
    </template>
    <p v-else class="refused-send-detail" data-testid="refused-send-posted">
      ✓ Posted as a comment on
      <RouterLink :to="refused.posted.value.href" class="underline" data-testid="refused-send-link">task #{{ refused.posted.value.taskId }}</RouterLink>
      — its next run will receive it.
    </p>
  </div>
</template>

<style scoped>
.refused-send {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0 0.75rem 0.5rem;
  padding: 8px 10px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-surface);
}

.refused-send-detail {
  margin: 0;
  font-size: 12px;
  line-height: 1.45;
  color: var(--color-fg-muted);
}
</style>
