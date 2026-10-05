<script setup>
// /files — the full-page viewer. Every piece of state is read from the URL
// (the source from the query, the line anchor from the hash), so a reload or a
// pasted link reproduces the view (I8). The left pane is the file browser's
// slot (D11b): present in the layout, empty and collapsed until that feature
// fills it.
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import FileViewer from '../components/files/FileViewer.vue'
import { sourceFromQuery } from '../components/files/source'

const route = useRoute()
const source = computed(() => sourceFromQuery(route.query))
const anchor = computed(() => route.hash || '')
</script>

<template>
  <div class="files-page" data-testid="files-page">
    <aside class="files-tree" data-testid="files-tree-pane" data-collapsed="true"
           aria-label="Files" hidden />
    <div class="files-main">
      <FileViewer v-if="source" :source="source" :anchor="anchor" />
      <p v-else class="files-empty" data-testid="files-empty">
        No file selected. Open one from a file reference, a trace, or search.
      </p>
    </div>
  </div>
</template>

<style scoped>
.files-page {
  display: flex;
  margin: 1rem;
  min-height: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--color-surface);
}
.files-tree { width: 0; flex-shrink: 0; }
.files-main { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.files-empty { padding: 1.5rem; font-size: 13px; color: var(--color-fg-muted); }
</style>
