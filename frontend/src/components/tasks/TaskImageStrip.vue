<script setup>
// The thumbnails and the attach button, for every surface that edits an
// owning row's images. Paste and drop are bound by the PARENT, on the form the
// operator is actually typing in — a strip that is empty until the first image
// has nothing to paste into.
//
// A thumbnail is 4.5rem, which is enough to tell two screenshots apart and not
// enough to read one, so clicking it opens the picture full size. The lightbox
// lives here rather than in each parent: every surface that shows the strip
// needs it, and a preview that exists on only some of them is the defect.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import Icon from '../ui/Icon.vue'

defineProps({
  items: { type: Array, required: true },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['pick', 'remove'])

const fileEl = ref(null)
const zoomed = ref(null)

function onPick(e) {
  emit('pick', e.target)
  e.target.value = ''
}
</script>

<template>
  <div class="task-images">
    <div v-if="items.length" class="task-image-strip" data-testid="task-image-strip">
      <span v-for="img in items" :key="img.key" class="task-image-chip">
        <Button
          variant="ghost"
          class="task-image-zoom"
          :aria-label="`Open ${img.name} full size`"
          data-testid="task-image-thumb"
          @click="zoomed = img"
        >
          <img :src="img.url" :alt="img.name" class="task-image-thumb">
        </Button>
        <!-- The index is the name an `![](image:N)` reference uses, so the
             hint that points at "each thumbnail" has to be true here. A held
             file carries one too: the composer writes the reference at the
             moment of the paste, and the server is asked for that index when
             the row it hangs off finally exists. -->
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

    <Dialog
      v-if="zoomed"
      :open="true"
      :title="zoomed.name"
      :description="zoomed.idx !== null && zoomed.idx !== undefined
        ? `Embed it with ![](image:${zoomed.idx})` : 'Not attached yet.'"
      data-testid="task-image-lightbox"
      @update:open="v => !v && (zoomed = null)"
    >
      <img :src="zoomed.url" :alt="zoomed.name" class="task-image-full">
    </Dialog>
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
.task-image-zoom {
    display: block;
    height: auto;
    padding: 0;
    border: 0;
    background: none;
    cursor: zoom-in;
}
.task-image-thumb {
    display: block;
    width: 4.5rem;
    height: 4.5rem;
    object-fit: cover;
}
.task-image-full {
    display: block;
    max-width: 100%;
    max-height: 70vh;
    margin: 0 auto;
    object-fit: contain;
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
