<script setup>
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import SearchRowTag from './SearchRowTag.vue'
import SearchLinePreview from './SearchLinePreview.vue'
import SearchSpanPreview from './SearchSpanPreview.vue'
import SearchFilePreview from './SearchFilePreview.vue'
import { KIND_META } from '../../utils/searchDocs.js'

const props = defineProps({
  row: { type: Object, default: null },
  query: { type: String, default: '' },
})
defineEmits(['open'])

const doc = computed(() => props.row?.doc || null)
const meta = computed(() => KIND_META[doc.value?.kind] || KIND_META.span)

const hitLabel = computed(() => {
  const n = props.row?.hits || 0
  if (n) return `${n} ${n === 1 ? 'match' : 'matches'} in body`
  return props.query ? 'matched on title' : 'preview'
})

const OPEN_LABEL = {
  span: 'Open span in trace',
  pattern: 'Open pattern',
  session: 'Open session',
  file: 'Open file',
}
const openLabel = computed(() => OPEN_LABEL[doc.value?.kind] || 'Open')
</script>

<template>
  <div class="search-preview">
    <template v-if="doc">
      <div class="preview-head">
        <div class="preview-kind">
          <Icon
            class="preview-glyph"
            :name="meta.icon"
            :size="14"
            :style="{ color: meta.accent }"
          />
          <span class="preview-kind-label">{{ meta.label }}</span>
          <SearchRowTag v-if="doc.tag" :kind="doc.kind" :tag="doc.tag" />
          <span class="preview-hits">{{ hitLabel }}</span>
        </div>
        <h2 class="preview-title">{{ doc.title }}</h2>
        <p v-if="doc.crumb" class="preview-crumb">{{ doc.crumb }}</p>
      </div>

      <!-- Each kind gets the renderer it already has elsewhere in the app; only
           the kinds regin has no richer view of stay as marked lines. -->
      <div class="preview-body content-scroll" :class="{ 'is-mono': doc.mono }">
        <!-- Keyed per document: these subtrees are mutated in place by the
             highlight directive, so patching one row's render into another's
             would reconcile against nodes the marking moved. -->
        <SearchSpanPreview v-if="doc.kind === 'span'" :key="doc.id" :doc="doc" :query="query" />
        <SearchFilePreview v-else-if="doc.kind === 'file'" :key="doc.id" :doc="doc" :query="query" />
        <SearchLinePreview v-else :key="doc.id" :doc="doc" :query="query" />
      </div>

      <div class="preview-foot">
        <button type="button" class="preview-open" @click="$emit('open', row)">
          {{ openLabel }}<kbd>↵</kbd>
        </button>
      </div>
    </template>

    <div v-else class="preview-explainer">
      <h2>Full-text search</h2>
      <p>
        One palette over two questions. <strong>This session</strong> reads its
        spans — prose, thinking, diffs, shell output and rule checks — and the
        files it touched; <strong>Everywhere</strong> reads wiki pages, every
        other session and files touched anywhere.
      </p>
      <dl class="preview-legend">
        <div><dt><kbd>↑</kbd><kbd>↓</kbd></dt><dd>move between results</dd></div>
        <div><dt><kbd>↵</kbd></dt><dd>open the highlighted result</dd></div>
        <div><dt><kbd>tab</kbd></dt><dd>cycle the kind</dd></div>
        <div><dt><kbd>ctrl</kbd><kbd>s</kbd></dt><dd>switch the scope</dd></div>
        <div><dt><kbd>esc</kbd></dt><dd>close, keeping the query</dd></div>
      </dl>
    </div>
  </div>
</template>

<style scoped>
.search-preview {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--color-background);
}

.preview-head {
  flex-shrink: 0;
  padding: 1rem 1.375rem 0.75rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}

.preview-kind { display: flex; align-items: center; gap: 0.5625rem; }

/* Scoped CSS reaches a child component's ROOT element — the Icon's <svg>. */
.preview-glyph { flex-shrink: 0; }

.preview-kind-label {
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-fg-muted);
  white-space: nowrap;
}

.preview-hits {
  margin-left: auto;
  font-size: 0.65625rem;
  color: var(--color-fg-subtle);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  white-space: nowrap;
}

.preview-title {
  font-size: 1rem;
  font-weight: 700;
  color: var(--color-fg);
  line-height: 1.35;
  margin: 0.5625rem 0 0;
  overflow-wrap: anywhere;
}

.preview-crumb {
  font-size: 0.71875rem;
  color: var(--color-fg-muted);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  margin: 0.3125rem 0 0;
  overflow-wrap: anywhere;
}

.preview-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: auto;
  padding: 0.875rem 1.25rem 1.125rem;
}

.preview-foot {
  flex-shrink: 0;
  padding: 0.75rem 1.375rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
}

.preview-open {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-white);
  background: var(--color-fg);
  border: 0;
  border-radius: 9px;
  padding: 0.4375rem 0.875rem;
  cursor: pointer;
}

.preview-open kbd {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.6875rem;
  opacity: 0.7;
}

.preview-explainer {
  margin: auto;
  padding: 2rem;
  max-width: 26rem;
  text-align: left;
}

.preview-explainer h2 {
  font-size: 0.9375rem;
  font-weight: 700;
  color: var(--color-fg);
  margin: 0 0 0.5rem;
}

.preview-explainer p {
  font-size: 0.8125rem;
  line-height: 1.65;
  color: var(--color-fg-muted);
  margin: 0 0 1rem;
}

.preview-legend { margin: 0; display: grid; gap: 0.375rem; }

.preview-legend > div { display: flex; align-items: center; gap: 0.625rem; }

.preview-legend dt { display: flex; gap: 3px; margin: 0; min-width: 4rem; }

.preview-legend dd {
  margin: 0;
  font-size: 0.75rem;
  color: var(--color-fg-muted);
}

kbd {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.625rem;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  padding: 1px 5px;
  border-radius: 4px;
}
</style>
