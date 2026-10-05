<script setup>
// Media kinds render natively from a blob URL. The bytes are fetched with the
// auth header (an <img src> cannot carry one), and the blob keeps the server's
// Content-Type, so the browser renders what the classifier decided rather than
// what it would sniff. An SVG goes through <img> only: an image document never
// runs its scripts (I2). A PDF opens in the browser's own viewer (D3).
import { onBeforeUnmount, onMounted, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import { rawUrl } from './source'

const props = defineProps({
  meta: { type: Object, required: true },
  source: { type: Object, required: true },
  lines: { type: Object, default: null },
})
const emit = defineEmits(['failed'])

const url = ref('')
const error = ref('')
const actualSize = ref(false)
let disposed = false

onMounted(async () => {
  try {
    const blobUrl = await api.getBlobUrl(rawUrl(props.source))
    if (disposed) {
      URL.revokeObjectURL(blobUrl)
      return
    }
    url.value = blobUrl
  } catch (err) {
    error.value = err.message || 'The file could not be loaded.'
    emit('failed', error.value)
  }
})

onBeforeUnmount(() => {
  disposed = true
  if (url.value) URL.revokeObjectURL(url.value)
})
</script>

<template>
  <div class="fmv" data-testid="file-media-view" :data-preview="meta.preview">
    <p v-if="error" class="fmv-note fmv-error" role="alert">{{ error }}</p>
    <p v-else-if="!url" class="fmv-note" role="status">Loading…</p>

    <template v-else-if="meta.preview === 'image' || meta.preview === 'svg'">
      <div class="fmv-bar">
        <Button size="sm" variant="secondary" data-testid="file-media-zoom"
                :aria-pressed="actualSize ? 'true' : 'false'" @click="actualSize = !actualSize">
          {{ actualSize ? 'Fit' : '1:1' }}
        </Button>
      </div>
      <div class="fmv-stage" :class="{ 'fmv-stage-actual': actualSize }">
        <img :src="url" :alt="meta.name" class="fmv-img" :class="{ 'fmv-img-fit': !actualSize }"
             data-testid="file-media-img">
      </div>
    </template>

    <iframe v-else-if="meta.preview === 'pdf'" :src="url" :title="meta.name"
            class="fmv-pdf" data-testid="file-media-pdf" />

    <div v-else-if="meta.preview === 'audio'" class="fmv-stage">
      <audio :src="url" controls preload="metadata" data-testid="file-media-audio" />
    </div>

    <div v-else-if="meta.preview === 'video'" class="fmv-stage">
      <video :src="url" controls preload="metadata" class="fmv-video" data-testid="file-media-video" />
    </div>
  </div>
</template>

<style scoped>
.fmv {
  flex: 1;
  min-height: 20rem;
  display: flex;
  flex-direction: column;
}
.fmv-note { padding: 1rem 1.25rem; font-size: 13px; color: var(--color-fg-muted); }
.fmv-error { color: var(--color-danger); }
.fmv-bar {
  display: flex;
  justify-content: flex-end;
  padding: 0.4rem 0.85rem;
  border-bottom: 1px solid var(--color-border);
}
.fmv-stage {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  overflow: auto;
  background: var(--color-surface-2);
}
.fmv-stage-actual { align-items: flex-start; justify-content: flex-start; }
.fmv-img-fit { max-width: 100%; max-height: 100%; object-fit: contain; }
.fmv-pdf { flex: 1; min-height: 0; width: 100%; border: 0; }
.fmv-video { max-width: 100%; max-height: 100%; }
</style>
