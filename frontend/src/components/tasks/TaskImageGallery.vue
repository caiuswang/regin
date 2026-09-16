<script setup>
// A task's attachments, on the page that has its address.
//
// Read-first, unlike `TaskImageStrip`: the detail page is where a task is
// looked at, so a thumbnail opens full size rather than offering a remove
// button it would be easy to hit by accident. Attaching and detaching stay on
// the edit surfaces.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Dialog from '../ui/Dialog.vue'

defineProps({
  items: { type: Array, required: true },
})

const zoomed = ref(null)
</script>

<template>
  <Card v-if="items.length">
    <h2 class="card-header">Images</h2>
    <div class="tig-grid" data-testid="task-image-gallery">
      <Button
        v-for="img in items"
        :key="img.key"
        variant="ghost"
        class="tig-cell"
        :aria-label="`Open ${img.name} full size`"
        data-testid="task-gallery-thumb"
        @click="zoomed = img"
      >
        <img :src="img.url" :alt="img.name" class="tig-thumb">
        <!-- The index is the name an `![](image:N)` reference in the body uses,
             so it has to be readable here or the syntax is unguessable. -->
        <span v-if="img.idx !== null" class="tig-idx">image:{{ img.idx }}</span>
      </Button>
    </div>
  </Card>

  <Dialog
    v-if="zoomed"
    :open="true"
    :title="zoomed.name"
    description="Attached to this task."
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
.tig-cell {
    position: relative;
    display: block;
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
.tig-full {
    display: block;
    max-width: 100%;
    max-height: 70vh;
    margin: 0 auto;
    object-fit: contain;
}
</style>
