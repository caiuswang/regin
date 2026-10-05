<script setup>
// The download_only state: metadata and a Download, no content. Shared by
// `binary` and `too_large`. Download goes through a blob because the raw route
// is behind a bearer token, which a plain <a href> cannot send.
import { onBeforeUnmount, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import { formatBytes, rawUrl } from './source'

const props = defineProps({
  meta: { type: Object, required: true },
  source: { type: Object, required: true },
  lines: { type: Object, default: null },
})

const busy = ref(false)
const error = ref('')
let lastUrl = ''

async function download() {
  busy.value = true
  error.value = ''
  try {
    if (lastUrl) URL.revokeObjectURL(lastUrl)
    lastUrl = await api.getBlobUrl(rawUrl(props.source, { download: '1' }))
    const a = document.createElement('a')
    a.href = lastUrl
    a.download = props.meta.name
    a.click()
  } catch (err) {
    error.value = err.message || 'Download failed.'
  } finally {
    busy.value = false
  }
}

onBeforeUnmount(() => { if (lastUrl) URL.revokeObjectURL(lastUrl) })
</script>

<template>
  <div class="fbc" data-testid="file-binary-card" :data-preview="meta.preview">
    <dl class="fbc-meta">
      <dt>Name</dt><dd data-testid="file-binary-name">{{ meta.name }}</dd>
      <dt>Size</dt><dd data-testid="file-binary-size">{{ formatBytes(meta.size) }}</dd>
      <dt>Type</dt><dd data-testid="file-binary-mime">{{ meta.mime }}</dd>
    </dl>
    <p class="fbc-why">
      {{ meta.preview === 'too_large'
        ? 'This file is too large to preview.'
        : 'This file is binary and has no preview.' }}
    </p>
    <div>
      <Button variant="primary" size="sm" :disabled="busy" data-testid="file-binary-download"
              @click="download">Download</Button>
    </div>
    <p v-if="error" class="fbc-error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.fbc { display: flex; flex-direction: column; gap: 0.75rem; padding: 1.25rem; max-width: 36rem; }
.fbc-meta {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.3rem 1rem;
  font-size: 13px;
}
.fbc-meta dt { color: var(--color-fg-muted); }
.fbc-meta dd { font-family: var(--font-mono, monospace); overflow-wrap: anywhere; }
.fbc-why { font-size: 13px; color: var(--color-fg-muted); }
.fbc-error { font-size: 12px; color: var(--color-danger); }
</style>
