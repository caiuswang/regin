<script setup>
// The mockups a feature carries, and the gesture that adds one.
//
// Its own card rather than a strip inside `FeatureSpec`, because the spec
// renders as prose until somebody presses Edit: an attach control that only
// exists while a textarea is open would hide the images from every reader of
// the page, and paste would have nothing to land in.
//
// The strip itself is the tasks surface's — same three props, same two events.
// Its class and testid names still say `task-image-*`; they are load-bearing
// in the task E2E specs, so the component was left where it was rather than
// renamed under them.
import { watch } from 'vue'
import Card from '../Card.vue'
import TaskImageStrip from '../tasks/TaskImageStrip.vue'
import { useAttachedImages } from '../../composables/useAttachedImages.js'
import { imageFilesFrom } from '../../utils/imageFiles'

const props = defineProps({
  featureId: { type: Number, required: true },
  images: { type: Array, default: () => [] },
  canWrite: { type: Boolean, default: false },
})

const {
  items, busy, error, bind, add, remove,
} = useAttachedImages('features')

watch(
  () => [props.featureId, props.images],
  () => bind({ id: props.featureId, images: props.images }),
  { immediate: true },
)

async function onPaste(e) {
  if (!props.canWrite || !imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  await add(e.clipboardData)
}

async function onDrop(e) {
  if (!props.canWrite || !imageFilesFrom(e.dataTransfer).length) return
  e.preventDefault()
  await add(e.dataTransfer)
}
</script>

<template>
  <!-- The gestures ride the Card, not an inner div: the whole card is the
       drop target, and a plain div carrying a listener reads to the
       convention checker as a clickable surface with no affordance. -->
  <Card
    class="fi-zone"
    data-testid="feature-images"
    @paste="onPaste"
    @dragover.prevent
    @drop="onDrop"
  >
    <h2 class="card-header fi-title">Images</h2>
    <p class="fi-sub">
      delivered with every run cut from this feature, ahead of whatever the
      slice carries itself
    </p>

    <!-- The empty line is not the strip's `v-else`: an editor with no images
         needs both the sentence and the attach control, and chaining them
         hid the sentence from everyone who could act on it. -->
    <p v-if="!items.length" class="fi-empty" data-testid="feature-images-empty">
      None. A spec that says “match the mockup” with no mockup attached
      leaves the agent nothing to match.
    </p>
    <div v-else-if="!canWrite" class="fi-readonly">
      <img
        v-for="img in items"
        :key="img.key"
        :src="img.url"
        :alt="img.name"
        class="fi-thumb"
      >
    </div>
    <TaskImageStrip
      v-if="canWrite"
      :items="items"
      :busy="busy"
      :error="error"
      @pick="add"
      @remove="remove"
    />
  </Card>
</template>

<style scoped>
.fi-zone { display: flex; flex-direction: column; gap: 0.35rem; }
.fi-title { font-size: 0.9rem; font-weight: 600; color: var(--color-fg); }
.fi-sub {
    font-size: 11.5px; color: var(--color-fg-subtle);
    margin-bottom: 0.35rem;
}
.fi-readonly { display: flex; flex-wrap: wrap; gap: 0.5rem; min-width: 0; }
.fi-thumb {
    display: block; width: 4.5rem; height: 4.5rem; object-fit: cover;
    border: 1px solid var(--color-border); border-radius: var(--radius-md);
}
.fi-empty { font-size: 0.8125rem; color: var(--color-fg-subtle); }
</style>
