<script setup>
// One result option. A process entity (feature / task / defect / session)
// arrives with a server-built `PaletteRow` and renders as one; a document
// (span / pattern / file) keeps its title-and-best-line shape.
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import SearchMark from './SearchMark.vue'
import SearchPaletteRow from './SearchPaletteRow.vue'
import SearchRowTag from './SearchRowTag.vue'
import {
  KIND_META, hitLines, plainLine, rowDomId, snippet,
} from '../../utils/searchDocs.js'

const props = defineProps({
  entry: { type: Object, required: true },
  query: { type: String, default: '' },
  active: { type: Boolean, default: false },
  // The busiest row in the list sets the hit bar's full width.
  peak: { type: Number, default: 1 },
})
defineEmits(['select', 'open'])

const doc = computed(() => props.entry.doc)
const meta = computed(() => KIND_META[doc.value.kind] || KIND_META.span)

// The BEST-matching line, not the document's first one. A page with eighty
// hits used to show its opening line, which at best repeated the title and
// at worst had no match in it at all — the one thing a result row exists to
// show. A heading line is skipped for the same reason: it IS the title.
const HEADING_RE = /^\s*#{1,6}\s/
function bestLine() {
  // Raw lines, because the heading test needs the `#` that `plainLine`
  // removes — stripping first makes every heading look like a body line and
  // the row goes back to echoing its own title. Strip after choosing.
  const lines = hitLines(doc.value.body, props.query, 12)
  const body = lines.find(h => !HEADING_RE.test(h.text)) || lines[0]
  return body ? plainLine(body.text, !!doc.value.mono) : ''
}

// A row whose only match is in its title still says something useful
// underneath — the sub line is the fallback, not an empty snippet.
const subPart = computed(() =>
  snippet((props.entry.hits ? bestLine() : '') || doc.value.sub, props.query))

// A path's filename is the identifying half and it sits at the END, which is
// exactly what right-truncation eats. Split it so the directory can shrink
// and the name cannot.
const SLASH = /^(.*\/)([^/]+)$/
const path = computed(() => {
  const m = doc.value.kind === 'file' ? String(doc.value.title || '').match(SLASH) : null
  return m ? { dir: m[1], name: m[2] } : { dir: '', name: doc.value.title }
})
const titlePart = computed(() => snippet(path.value.name, props.query, 24, 96))

// "matches", not "hits": the preview header counts the same number and says
// matches, and one quantity under two names in one pane is exactly the kind
// of thing the redesign is meant to remove.
const hitLabel = computed(() => {
  const n = props.entry.hits
  if (!n) return ''
  return n === 1 ? '1 match' : `${n} matches`
})
const hitWeight = computed(() =>
  `${Math.round(((props.entry.hits || 0) / props.peak) * 100)}%`)
</script>

<template>
  <div
    :id="rowDomId(entry.key)"
    role="option"
    :aria-selected="active"
    class="result-row cursor-pointer hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    :class="{ 'is-active': active, 'is-entity': !!doc.row }"
    @click="$emit('select', entry)"
    @dblclick="$emit('open', entry)"
  >
    <SearchPaletteRow v-if="doc.row" :row="doc.row" :query="query" />
    <template v-else>
      <Icon
        class="row-glyph"
        :name="meta.icon"
        :size="14"
        :style="{ color: meta.accent }"
      />
      <span class="row-main">
        <span class="row-title">
          <span v-if="path.dir" class="row-dir">{{ path.dir }}</span>
          <SearchMark class="row-name" :part="titlePart" />
        </span>
        <SearchMark class="row-sub" :part="subPart" tone="soft" />
      </span>
      <span class="row-meta">
        <span v-if="hitLabel" class="row-hits">
          <span class="row-bar" aria-hidden="true"><i :style="{ width: hitWeight }" /></span>
          {{ hitLabel }}
        </span>
        <SearchRowTag v-if="doc.tag" :kind="doc.kind" :tag="doc.tag" />
      </span>
    </template>
    <span v-if="active" class="row-enter">↵</span>
  </div>
</template>

<style scoped>
.result-row {
  display: flex;
  align-items: flex-start;
  gap: 0.6875rem;
  padding: 0.5625rem 0.75rem;
  border-radius: 11px;
  cursor: pointer;
}

.result-row:hover { background: var(--color-background); }

.result-row.is-active {
  background: var(--color-surface-selected);
  box-shadow: inset 2.5px 0 0 var(--color-primary);
}

/* Scoped CSS reaches a child component's ROOT element, which is what the
   Icon's <svg> is — so this styles the glyph without a global class. */
.row-glyph {
  flex-shrink: 0;
  margin-top: 0.125rem;
  opacity: 0.85;
}

.result-row.is-active .row-glyph,
.result-row:hover .row-glyph { opacity: 1; }

.row-main { flex: 1; min-width: 0; display: block; }

.row-title {
  display: flex;
  align-items: baseline;
  gap: 0.375rem;
  min-width: 0;
  font-size: var(--sp-t-body);
  line-height: 1.38;
  font-weight: 550;
  color: var(--color-fg);
}

/* The directory shrinks; the filename does not. */
.row-dir {
  flex-shrink: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.93em;
  font-weight: 500;
  color: var(--color-fg-faint);
}

.row-name {
  flex-shrink: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-sub {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  color: var(--color-fg-subtle);
  margin-top: 3px;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
}

.row-meta {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding-top: 1px;
}

/* One label at one size. It used to be a filled pill reading "48 matches",
   which competed with the tag beside it and with the group count above it
   for the same glance; the bar carries the comparison and the number carries
   the value. */
.row-hits {
  display: inline-flex;
  align-items: center;
  gap: 0.3125rem;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-micro);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--color-fg-muted);
  white-space: nowrap;
}

.row-bar {
  flex-shrink: 0;
  width: 1.625rem;
  height: 3px;
  border-radius: 2px;
  background: var(--color-surface-3);
  overflow: hidden;
}

.row-bar i { display: block; height: 100%; border-radius: 2px; background: var(--color-primary); }

.row-enter { flex-shrink: 0; padding-top: 1px; font-size: var(--sp-t-micro); color: var(--color-primary); }
</style>
