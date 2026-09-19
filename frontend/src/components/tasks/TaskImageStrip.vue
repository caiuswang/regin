<script setup>
// The thumbnails and the attach button, for both surfaces that edit a task's
// images. Paste and drop are bound by the PARENT, on the form the operator is
// actually typing in — a strip that is empty until the first image has nothing
// to paste into.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

defineProps({
  items: { type: Array, required: true },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['pick', 'remove'])

const fileEl = ref(null)

function onPick(e) {
  emit('pick', e.target)
  e.target.value = ''
}
</script>

<template>
  <div class="task-images">
    <div v-if="items.length" class="task-image-strip" data-testid="task-image-strip">
      <span v-for="img in items" :key="img.key" class="task-image-chip">
        <img :src="img.url" :alt="img.name" class="task-image-thumb">
        <!-- The index is the name an `![](image:N)` reference uses, so the
             hint that points at "each thumbnail" has to be true here. Absent
             on a held file: a composer's images have no idx until the row
             they hang off exists. -->
        <span
          v-if="img.idx !== null && img.idx !== undefined"
          class="task-image-idx"
        >image:{{ img.idx }}</span>
        <Button
          variant="ghost"
          size="icon"
          class="task-image-x"
          :aria-label="`Remove ${img.name}`"
          @click="emit('remove', img)"
        >&times;</Button>
      </span>
    </div>
    <div class="task-image-actions">
      <input
        ref="fileEl"
        type="file"
        accept="image/*"
        multiple
        class="sr-only"
        tabindex="-1"
        aria-hidden="true"
        data-testid="task-image-file"
        @change="onPick"
      >
      <Button
        variant="secondary"
        size="sm"
        :disabled="busy"
        aria-label="Attach an image to this task"
        data-testid="task-image-attach"
        @click="fileEl?.click()"
      >
        <Icon name="image" :size="13" />
        {{ busy ? 'Attaching…' : 'Attach image' }}
      </Button>
      <span class="task-image-hint">or paste / drop one into this form</span>
    </div>
    <p v-if="error" class="task-image-error" role="alert" data-testid="task-image-error">
      {{ error }}
    </p>
  </div>
</template>

<style scoped>
.task-images {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    min-width: 0;
}
.task-image-idx {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 0 0.1875rem;
    font-size: 0.5625rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    line-height: 1.4;
    color: var(--color-fg-muted);
    background: var(--color-surface);
    text-align: left;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.task-image-strip {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    min-width: 0;
}
.task-image-chip {
    position: relative;
    display: inline-flex;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
    overflow: hidden;
}
.task-image-thumb {
    display: block;
    width: 4.5rem;
    height: 4.5rem;
    object-fit: cover;
}
.task-image-x {
    position: absolute;
    top: 0.125rem;
    right: 0.125rem;
    background: var(--color-surface);
}
.task-image-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
}
.task-image-hint {
    font-size: 0.6875rem;
    color: var(--color-fg-faint);
}
.task-image-error {
    font-size: 0.8125rem;
    color: var(--color-danger);
    overflow-wrap: anywhere;
}
</style>
