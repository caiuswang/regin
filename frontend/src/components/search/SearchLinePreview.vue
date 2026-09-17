<script setup>
// The LITERAL preview: a window of the document's body, one line per row,
// verbatim, with the query marked. It is what a body that must not reflow
// gets — a diff, shell output, a mono span — and what a span falls back to
// when its source object cannot be reached. Prose bodies go to
// SearchProsePreview instead, which renders the markdown they are written in.
import { computed, nextTick, ref, watch } from 'vue'
import SearchMark from './SearchMark.vue'
import { markLine, previewWindow } from '../../utils/searchDocs.js'

const props = defineProps({
  doc: { type: Object, required: true },
  query: { type: String, default: '' },
  // Which of the body's hit lines the preview's stepper is parked on. The
  // window follows it, so walking hits walks the document rather than
  // re-reading the same opening screen.
  hitIndex: { type: Number, default: 0 },
})

const win = computed(() => previewWindow(props.doc?.body || '', props.query, props.hitIndex))

const lines = computed(() =>
  win.value.lines.map((text, i) => ({ key: i, ...markLine(text, props.query) })))

const cursorEl = ref(null)
// A function ref, not a string one: inside `v-for` a string ref collects an
// array of every matching node, and the whole point here is the single line
// the stepper is parked on.
function bindCursor(el, isCursor) {
  if (isCursor) cursorEl.value = el
}

// The stepper moves the window, but a hit six lines down the window is still
// off the fold on a short pane — the scroll is what makes ⌥↓ mean "go there".
watch(() => [props.doc?.id, props.hitIndex], async () => {
  await nextTick()
  cursorEl.value?.scrollIntoView({ block: 'nearest' })
}, { immediate: true })
</script>

<template>
  <p v-if="win.elidedAbove" class="preview-elision">⋯ {{ win.elidedAbove }} lines above the hit</p>
  <div
    v-for="(line, i) in lines"
    :key="line.key"
    :ref="el => bindCursor(el, i === win.cursor)"
    class="preview-line"
    :class="{ 'is-hit': line.has, 'is-cursor': i === win.cursor }"
  >
    <span class="preview-bar" />
    <SearchMark class="preview-text" :part="line" :current="i === win.cursor" />
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

.preview-line.is-cursor {
  background: var(--color-surface-2);
  scroll-margin: 2.5rem 0;
}

.preview-line.is-cursor .preview-bar { background: var(--color-primary); }

.preview-text {
  flex: 1;
  min-width: 0;
  font-size: var(--sp-t-body);
  line-height: var(--sp-lh-body);
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
  font-size: 0.93em;
  line-height: 1.65;
}

.preview-elision {
  font-size: var(--sp-t-micro);
  color: var(--color-fg-faint);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  padding: 0 8px 8px;
  margin: 0;
}
</style>
