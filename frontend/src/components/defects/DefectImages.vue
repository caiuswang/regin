<script setup>
// The screenshots a defect was reported with, on the row that carries it.
//
// Its own component, mounted only when the row has images, because binding
// pulls one authenticated fetch per picture: a page of twenty reports where
// three carry evidence should cost three requests, not twenty mounts deciding
// they have nothing to do.
//
// Read-only. A defect is a claim about the past and its evidence is part of
// the claim — correcting it is `defects reattribute` or a new report, not a
// quiet swap of the picture underneath a blame that was already assigned.
//
// A row that also lets its symptom cite these pictures hands its own list in
// as `items`, so the strip and the inline pictures are one fetch and agree on
// what is attached.
import { computed, onMounted, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import { useAttachedImages } from '../../composables/useAttachedImages.js'

const props = defineProps({
  defect: { type: Object, required: true },
  items: { type: Array, default: null },
})

const own = useAttachedImages('defects')
const items = computed(() => props.items ?? own.items.value)
const zoomed = ref(null)

function bindOwn() {
  if (!props.items) own.bind(props.defect)
}
onMounted(bindOwn)
watch([() => props.defect.id, () => props.defect.images?.length], bindOwn)
</script>

<template>
  <div v-if="items.length" class="di-strip" data-testid="defect-images">
    <Button
      v-for="img in items"
      :key="img.key"
      variant="ghost"
      class="di-cell"
      :aria-label="`Open ${img.name} full size`"
      data-testid="defect-image-thumb"
      @click="zoomed = img"
    >
      <img :src="img.url" :alt="img.name" class="di-thumb">
    </Button>

    <Dialog
      v-if="zoomed"
      :open="true"
      :title="zoomed.name"
      :description="`Reported with defect #${defect.id}.`"
      data-testid="defect-image-lightbox"
      @update:open="v => !v && (zoomed = null)"
    >
      <img :src="zoomed.url" :alt="zoomed.name" class="di-full">
    </Dialog>
  </div>
</template>

<style scoped>
.di-strip { display: flex; flex-wrap: wrap; gap: 0.4rem; min-width: 0; }
.di-cell {
    display: block;
    height: auto;
    padding: 0;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface-2);
    overflow: hidden;
    cursor: zoom-in;
}
.di-cell:hover { border-color: var(--color-fg-faint); }
.di-thumb { display: block; width: 4rem; height: 4rem; object-fit: cover; }
.di-full {
    display: block;
    max-width: 100%;
    max-height: 70vh;
    margin: 0 auto;
    object-fit: contain;
}
</style>
