<script setup>
// A task's attachments, on the page that has its address.
//
// Read-first, unlike `TaskImageStrip`: the detail page is where a task is
// looked at, so a thumbnail opens full size rather than being a 4.5rem chip
// beside a form. The write gestures are here too, though, and that is the
// defect this closes — attaching and detaching used to live only on the edit
// modal, so the page a task is read on could show a picture and offer no way
// to replace it, while the feature page one level up could.
//
// The list belongs to the VIEW, shared with the body editor: the editor writes
// `![](image:N)` references against these same attachments, and two composable
// instances would let the reference and the thumbnail disagree about what is
// attached.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Dialog from '../ui/Dialog.vue'
import Icon from '../ui/Icon.vue'
import { imageFilesFrom } from '../../utils/imageFiles'

const props = defineProps({
  items: { type: Array, required: true },
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['pick', 'remove'])

const zoomed = ref(null)
const fileEl = ref(null)

function onPick(e) {
  emit('pick', e.target)
  e.target.value = ''
}

function onPaste(e) {
  if (!props.canWrite || !imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  emit('pick', e.clipboardData)
}

function onDrop(e) {
  if (!props.canWrite || !imageFilesFrom(e.dataTransfer).length) return
  e.preventDefault()
  emit('pick', e.dataTransfer)
}
</script>

<template>
  <!-- The gestures ride the Card, not an inner div: the whole card is the drop
       target, and a plain div carrying a listener reads to the convention
       checker as a clickable surface with no affordance. -->
  <Card
    v-if="items.length || canWrite"
    class="tig-zone"
    @paste="onPaste"
    @dragover.prevent
    @drop="onDrop"
  >
    <h2 class="card-header">Images</h2>
    <div v-if="items.length" class="tig-grid" data-testid="task-image-gallery">
      <span v-for="img in items" :key="img.key" class="tig-chip">
        <Button
          variant="ghost"
          class="tig-cell"
          :aria-label="`Open ${img.name} full size`"
          data-testid="task-gallery-thumb"
          @click="zoomed = img"
        >
          <img :src="img.url" :alt="img.name" class="tig-thumb">
          <!-- The index is the name an `![](image:N)` reference in the body
               uses, so it has to be readable here or the syntax is
               unguessable. -->
          <span v-if="img.idx !== null" class="tig-idx">image:{{ img.idx }}</span>
        </Button>
        <Button
          v-if="canWrite"
          variant="ghost"
          size="icon"
          class="tig-x"
          :aria-label="`Remove ${img.name}`"
          data-testid="task-gallery-remove"
          @click="emit('remove', img)"
        >&times;</Button>
      </span>
    </div>
    <p v-else class="tig-empty" data-testid="task-images-empty">
      None. Paste or drop a screenshot here, then name it from the body with
      an <code>![](image:N)</code> reference.
    </p>

    <div v-if="canWrite" class="tig-actions">
      <input
        ref="fileEl"
        type="file"
        accept="image/*"
        multiple
        class="sr-only"
        tabindex="-1"
        aria-hidden="true"
        data-testid="task-gallery-file"
        @change="onPick"
      >
      <Button
        variant="secondary"
        size="sm"
        :disabled="busy"
        aria-label="Attach an image to this task"
        data-testid="task-gallery-attach"
        @click="fileEl?.click()"
      >
        <Icon name="image" :size="13" />
        {{ busy ? 'Attaching…' : 'Attach image' }}
      </Button>
      <span class="tig-hint">or paste / drop one onto this card</span>
    </div>
    <p v-if="error" class="tig-error" role="alert" data-testid="task-gallery-error">
      {{ error }}
    </p>
  </Card>

  <Dialog
    v-if="zoomed"
    :open="true"
    :title="zoomed.name"
    :description="`Attached to this task. Embed it with ![](image:${zoomed.idx})`"
    data-testid="task-image-lightbox"
    @update:open="v => !v && (zoomed = null)"
  >
    <img :src="zoomed.url" :alt="zoomed.name" class="tig-full">
  </Dialog>
</template>

<style scoped>
.tig-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(7rem, 1fr));
    gap: 0.5rem;
    min-width: 0;
}
.tig-chip { position: relative; display: block; min-width: 0; }
.tig-cell {
    position: relative;
    display: block;
    width: 100%;
    height: auto;
    padding: 0;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
    overflow: hidden;
    cursor: zoom-in;
}
.tig-cell:hover { border-color: var(--color-fg-faint); }
.tig-thumb {
    display: block;
    width: 100%;
    height: 7rem;
    object-fit: cover;
}
.tig-idx {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 0.125rem 0.375rem;
    font-size: 0.625rem;
    font-family: var(--font-mono, ui-monospace, monospace);
    color: var(--color-fg-muted);
    background: var(--color-surface);
    text-align: left;
}
.tig-x {
    position: absolute;
    top: 0.25rem;
    right: 0.25rem;
    background: var(--color-surface);
}
.tig-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    margin-top: 0.6rem;
    min-width: 0;
}
.tig-hint { font-size: 0.6875rem; color: var(--color-fg-faint); }
.tig-empty { font-size: 0.8125rem; color: var(--color-fg-faint); }
.tig-empty code {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.75rem;
}
.tig-error {
    margin-top: 0.4rem;
    font-size: 0.8125rem;
    color: var(--color-danger);
    overflow-wrap: anywhere;
}
.tig-full {
    display: block;
    max-width: 100%;
    max-height: 70vh;
    margin: 0 auto;
    object-fit: contain;
}
</style>
