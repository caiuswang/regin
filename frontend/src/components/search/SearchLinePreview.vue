<script setup>
// The plain preview: a window of the document's body, one line per row, with
// the query marked. It is what a `pattern` or a `session` hit gets — those are
// documents regin has no richer renderer for — and what a span falls back to
// when its source object cannot be reached.
import { computed } from 'vue'
import SearchMark from './SearchMark.vue'
import { markLine, previewWindow } from '../../utils/searchDocs.js'

const props = defineProps({
  doc: { type: Object, required: true },
  query: { type: String, default: '' },
})

const win = computed(() => previewWindow(props.doc?.body || '', props.query))

const lines = computed(() =>
  win.value.lines.map((text, i) => ({ key: i, ...markLine(text, props.query) })))
</script>

<template>
  <p v-if="win.elidedAbove" class="preview-elision">⋯ {{ win.elidedAbove }} lines above</p>
  <div
    v-for="line in lines"
    :key="line.key"
    class="preview-line"
    :class="{ 'is-hit': line.has }"
  >
    <span class="preview-bar" />
    <SearchMark class="preview-text" :part="line" />
  </div>
  <p v-if="!lines.length" class="preview-elision">No preview lines for this result.</p>
  <p v-if="win.truncated" class="preview-elision">⋯ truncated</p>
</template>

<style scoped>
.preview-line { display: flex; gap: 0.5rem; border-radius: 5px; }

.preview-line.is-hit { background: var(--search-hit-line-bg); }

.preview-bar {
  flex-shrink: 0;
  width: 2px;
  border-radius: 2px;
  background: transparent;
}

.preview-line.is-hit .preview-bar { background: var(--search-hit-line-bar); }

.preview-text {
  flex: 1;
  min-width: 0;
  font-size: 0.8125rem;
  line-height: 1.62;
  color: var(--color-fg-muted);
  /* Diffs and shell output must not reflow. */
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  padding: 1px 6px 1px 0;
}

.preview-line.is-hit .preview-text { color: var(--color-fg); }

/* `.is-mono` sits on the pane wrapper in SearchPreview; scoping only pins the
   LAST compound selector, so the ancestor match still works from here. */
.is-mono .preview-text {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.71875rem;
  line-height: 1.65;
}

.preview-elision {
  font-size: 0.65625rem;
  color: var(--color-fg-subtle);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  padding: 0 8px 8px;
  margin: 0;
}
</style>
