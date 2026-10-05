<script setup>
// The side-drawer mount of the same FileViewer the /files page uses (D8).
// Its state is the shared `useFilePreview` store; "Open page" hands the same
// source to /files, where it becomes URL-addressed.
import { computed, onBeforeUnmount, watch } from 'vue'
import Button from '../ui/Button.vue'
import FileViewer from './FileViewer.vue'
import { useFilePreview } from '../../composables/useFilePreview'
import { sourceQueryString } from './source'

const { open, source, anchor, closeFilePreview } = useFilePreview()
const pageHref = computed(() => (source.value
  ? `/files?${sourceQueryString(source.value)}${anchor.value || ''}`
  : '/files'))

// Escape inside the editor belongs to Monaco (find widget, read-only notice).
function onKey(event) {
  if (event.key !== 'Escape') return
  if (event.target instanceof Element && event.target.closest('.monaco-editor')) return
  closeFilePreview()
}
watch(open, (isOpen) => {
  if (isOpen) window.addEventListener('keydown', onKey)
  else window.removeEventListener('keydown', onKey)
}, { immediate: true })
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div v-if="open && source" class="fpd-backdrop">
      <Button variant="ghost" class="fpd-scrim" aria-label="Close file preview" @click="closeFilePreview" />
      <aside class="fpd" role="dialog" aria-modal="true" aria-label="File preview"
             data-testid="file-preview-drawer">
        <FileViewer :source="source" :anchor="anchor">
          <template #actions>
            <router-link :to="pageHref" class="fpd-link" data-testid="file-preview-open-page"
                         @click="closeFilePreview">Open page</router-link>
            <Button variant="ghost" size="sm" aria-label="Close" data-testid="file-preview-close"
                    @click="closeFilePreview">✕</Button>
          </template>
        </FileViewer>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.fpd-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal, 50);
  background: rgba(var(--scrim-rgb), 0.35);
  display: flex;
  justify-content: flex-end;
}
.fpd-backdrop .fpd-scrim {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border-radius: 0;
  background: transparent;
}
.fpd {
  position: relative;
  width: min(56rem, 100vw);
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  box-shadow: var(--ds-shadow-lg);
}
.fpd-link { font-size: 12px; color: var(--color-link, var(--color-fg)); }
</style>
