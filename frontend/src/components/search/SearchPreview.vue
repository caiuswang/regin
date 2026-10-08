<script setup>
import { computed, watch } from 'vue'
import Icon from '../ui/Icon.vue'
import SearchRowTag from './SearchRowTag.vue'
import SearchLinePreview from './SearchLinePreview.vue'
import SearchProsePreview from './SearchProsePreview.vue'
import SearchSpanPreview from './SearchSpanPreview.vue'
import SearchFilePreview from './SearchFilePreview.vue'
import SearchHitList from './SearchHitList.vue'
import SearchEntityPreview from './SearchEntityPreview.vue'
import { HIT_LINE_CAP, KIND_META, hitLines } from '../../utils/searchDocs.js'

const props = defineProps({
  row: { type: Object, default: null },
  query: { type: String, default: '' },
  // Which of this document's hit lines the stepper is parked on, and whether
  // the pane is reading the document or listing its hits. Both are owned by
  // the palette, because both are driven by keys the palette traps.
  hitIndex: { type: Number, default: 0 },
  mode: { type: String, default: 'read' },
  // A process entity previews through the walk (useEntityWalk): its stack,
  // the picked relation and the R6 actions. Empty for every document kind.
  entityStack: { type: Array, default: () => [] },
  pickKey: { type: String, default: null },
  actions: { type: Object, default: null },
  copied: { type: Boolean, default: false },
})
const emit = defineEmits([
  'open', 'open-file', 'step', 'pick-hit', 'mode',
  'follow', 'pop-to', 'act', 'go', 'retry',
])

const doc = computed(() => props.row?.doc || null)
const meta = computed(() => KIND_META[doc.value?.kind] || KIND_META.span)

// The stepper moves a WINDOW, so it only exists where the renderer is one:
// `SearchFilePreview` hands the body to DiffBlock whole and the rich span
// renderer paints a view model, neither of which has a line to scroll to.
// Both still get the hit list, which reads the indexed body like every
// other kind.
const RENDERER = { span: 'span', file: 'file' }
const renderer = computed(() => {
  if (!doc.value) return 'none'
  const special = RENDERER[doc.value.kind]
  if (special) return special
  return doc.value.mono ? 'line' : 'prose'
})
// The renderer decides how a line is CITED, not just how it is drawn: a
// prose body is markdown, so the hit list has to quote what the reader will
// see rather than the syntax that produces it.
const hits = computed(() => hitLines(doc.value?.body || '', props.query, undefined,
  { plain: renderer.value === 'prose' }))
const capped = computed(() => hits.value.length >= HIT_LINE_CAP)

// Two quantities that are NOT the same and must never be printed as one:
// `matches` counts occurrences (what the row badge ranks on), `lines` counts
// places to look (what the stepper walks). A paragraph holding three
// occurrences is one place to go.
const hitLabel = computed(() => {
  const n = props.row?.hits || 0
  const lines = hits.value.length
  if (!n) return props.query ? 'matched on title' : 'preview'
  if (!lines) return `${n} ${n === 1 ? 'match' : 'matches'}`
  return `${n} ${n === 1 ? 'match' : 'matches'} · ${lines} ${lines === 1 ? 'line' : 'lines'}`
})

const steppable = computed(() =>
  hits.value.length > 1 && (renderer.value === 'line' || renderer.value === 'prose'))

const position = computed(() => `${Math.min(props.hitIndex + 1, hits.value.length)} / ${hits.value.length}`)

// A shorter document can leave the stepper pointing past the end.
watch(() => [doc.value?.id, hits.value.length], () => {
  if (props.hitIndex >= hits.value.length) emit('step', -props.hitIndex)
})

const OPEN_LABEL = {
  span: 'Open span in trace',
  pattern: 'Open pattern',
  session: 'Open session',
  // The footer follows the row's href, which is the edit span; the file
  // itself opens from the preview's own Open file.
  file: 'Open edit in trace',
}
const openLabel = computed(() => OPEN_LABEL[doc.value?.kind] || 'Open')
</script>

<template>
  <SearchEntityPreview
    v-if="entityStack.length"
    :stack="entityStack"
    :pick-key="pickKey"
    :actions="actions"
    :copied="copied"
    @follow="k => emit('follow', k)"
    @pop-to="d => emit('pop-to', d)"
    @act="w => emit('act', w)"
    @go="h => emit('go', h)"
    @retry="() => emit('retry')"
  />
  <div v-else class="search-preview">
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

        <div v-if="hits.length" class="preview-tools">
          <button
            type="button"
            class="preview-flip focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            :aria-pressed="mode === 'hits'"
            @click="$emit('mode', mode === 'read' ? 'hits' : 'read')"
          >
            <Icon :name="mode === 'read' ? 'list' : 'file'" :size="13" />
            {{ mode === 'read' ? 'Hit list' : 'Read' }}<kbd>⌥L</kbd>
          </button>

          <div v-if="steppable" class="preview-stepper" role="group" aria-label="Walk hits">
            <button
              type="button"
              class="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Previous hit"
              @click="$emit('step', -1)"
            ><Icon name="arrow-up" :size="11" /></button>
            <span class="preview-pos">{{ position }}</span>
            <button
              type="button"
              class="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Next hit"
              @click="$emit('step', 1)"
            ><Icon name="arrow-down" :size="11" /></button>
            <span class="preview-chord" aria-hidden="true">⌥↑↓</span>
          </div>
        </div>
      </div>

      <SearchHitList
        v-if="mode === 'hits'"
        class="preview-body content-scroll"
        :hits="hits"
        :active-index="hitIndex"
        :capped="capped"
        @pick="i => $emit('pick-hit', i)"
      />

      <!-- Each kind gets the renderer it already has elsewhere in the app; only
           the kinds regin has no richer view of stay as marked lines. -->
      <div v-else class="preview-body content-scroll" :class="{ 'is-mono': doc.mono }">
        <!-- Keyed per document: these subtrees are mutated in place by the
             highlight directive, so patching one row's render into another's
             would reconcile against nodes the marking moved. -->
        <SearchSpanPreview v-if="renderer === 'span'" :key="doc.id" :doc="doc" :query="query" />
        <SearchFilePreview
          v-else-if="renderer === 'file'"
          :key="doc.id"
          :doc="doc"
          :query="query"
          :hit-line="hits[hitIndex]?.line || 0"
          @open-file="(source, anchor) => $emit('open-file', source, anchor)"
        />
        <SearchProsePreview
          v-else-if="renderer === 'prose'"
          :key="doc.id"
          :doc="doc"
          :query="query"
          :hit-index="hitIndex"
        />
        <SearchLinePreview v-else :key="doc.id" :doc="doc" :query="query" :hit-index="hitIndex" />
      </div>

      <div class="preview-foot">
        <button
          type="button"
          class="preview-open focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          @click="$emit('open', row)"
        >
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
        <div><dt><kbd>⌥</kbd><kbd>↑</kbd><kbd>↓</kbd></dt><dd>walk the hits inside one result</dd></div>
        <div><dt><kbd>⌥</kbd><kbd>L</kbd></dt><dd>list every hit, instead of reading</dd></div>
        <div><dt><kbd>tab</kbd></dt><dd>cycle the kind</dd></div>
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
  padding: 1rem 1.375rem 0;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}

.preview-kind { display: flex; align-items: center; gap: 0.4375rem; }

/* Scoped CSS reaches a child component's ROOT element — the Icon's <svg>. */
.preview-glyph { flex-shrink: 0; }

.preview-kind-label {
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  font-weight: 700;
  letter-spacing: 0.085em;
  text-transform: uppercase;
  color: var(--color-fg-muted);
  white-space: nowrap;
}

.preview-hits {
  margin-left: auto;
  font-size: var(--sp-t-micro);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-variant-numeric: tabular-nums;
  color: var(--color-fg-faint);
  white-space: nowrap;
}

.preview-title {
  font-size: var(--sp-t-display);
  line-height: var(--sp-lh-display);
  font-weight: 650;
  letter-spacing: -0.015em;
  color: var(--color-fg);
  margin: 0.4375rem 0 0;
  overflow-wrap: anywhere;
}

.preview-crumb {
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  color: var(--color-fg-faint);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  margin: 0.3125rem 0 0;
  overflow-wrap: anywhere;
}

.preview-tools {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.75rem;
  padding-bottom: 0.625rem;
}

.preview-flip {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  height: 1.625rem;
  padding: 0 0.5625rem;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: 7px;
  font-size: var(--sp-t-meta);
  line-height: 1;
  font-weight: 600;
  color: var(--color-fg-muted);
  cursor: pointer;
  transition: border-color 180ms, color 180ms, background-color 180ms;
}

.preview-flip:hover { border-color: var(--color-primary); color: var(--color-fg); }

.preview-stepper {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 1.625rem;
  padding: 2px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: 7px;
}

.preview-stepper button {
  display: grid;
  place-items: center;
  width: 1.25rem;
  height: 1.125rem;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--color-fg-muted);
  cursor: pointer;
  transition: background-color 150ms, color 150ms;
}

.preview-stepper button:hover { background: var(--color-surface); color: var(--color-fg); }

.preview-pos {
  padding: 0 0.3125rem;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-micro);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--color-fg-muted);
}

.preview-chord {
  padding: 0 0.3125rem 0 0.125rem;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-kbd);
  color: var(--color-fg-faint);
}

.preview-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: auto;
  padding: 0.875rem 1.375rem 1.25rem;
}

.preview-foot {
  flex-shrink: 0;
  padding: 0.6875rem 1.375rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-surface);
}

.preview-open {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  height: 2rem;
  padding: 0 0.8125rem;
  font-size: var(--sp-t-meta);
  font-weight: 600;
  color: var(--color-primary-fg);
  background: var(--color-primary);
  border: 0;
  border-radius: 8px;
  cursor: pointer;
  transition: background-color 180ms;
}

.preview-open:hover { background: var(--color-primary-hover); }

.preview-open kbd {
  background: rgb(255 255 255 / 0.2);
  border-color: transparent;
  color: inherit;
}

.preview-explainer {
  margin: auto;
  padding: 2rem;
  max-width: 26rem;
  text-align: left;
}

.preview-explainer h2 {
  font-size: var(--sp-t-body);
  font-weight: 700;
  color: var(--color-fg);
  margin: 0 0 0.5rem;
}

.preview-explainer p {
  font-size: var(--sp-t-body);
  line-height: var(--sp-lh-body);
  color: var(--color-fg-muted);
  margin: 0 0 1rem;
}

.preview-legend { margin: 0; display: grid; gap: 0.375rem; }

.preview-legend > div { display: flex; align-items: center; gap: 0.625rem; }

.preview-legend dt { display: flex; gap: 3px; margin: 0; min-width: 4.5rem; }

.preview-legend dd {
  margin: 0;
  font-size: var(--sp-t-meta);
  color: var(--color-fg-muted);
}

kbd {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-kbd);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  padding: 1px 5px;
  border-radius: 4px;
}
</style>
