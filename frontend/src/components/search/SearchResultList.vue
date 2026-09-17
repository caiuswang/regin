<script setup>
import Icon from '../ui/Icon.vue'
import SearchMark from './SearchMark.vue'
import SearchRowTag from './SearchRowTag.vue'
import { computed } from 'vue'
import {
  KIND_META, MIN_QUERY_CHARS, OTHER_ROW_CAP, SPAN_ROW_CAP,
  hitLines, plainLine, snippet,
} from '../../utils/searchDocs.js'

const props = defineProps({
  groups: { type: Array, default: () => [] },
  query: { type: String, default: '' },
  activeId: { type: String, default: null },
  noMatches: { type: Boolean, default: false },
  canWiden: { type: Boolean, default: false },
  // The query is too short to run. Distinct from `noMatches`, which means it
  // ran and found nothing — the two want opposite things said.
  held: { type: Boolean, default: false },
})
defineEmits(['select', 'open', 'retry', 'widen'])

const meta = kind => KIND_META[kind] || KIND_META.span

// The BEST-matching line, not the document's first one. A page with eighty
// hits used to show its opening line, which at best repeated the title and
// at worst had no match in it at all — the one thing a result row exists to
// show. A heading line is skipped for the same reason: it IS the title.
const HEADING_RE = /^\s*#{1,6}\s/
const bestLine = (row) => {
  // Raw lines, because the heading test needs the `#` that `plainLine`
  // removes — stripping first makes every heading look like a body line and
  // the row goes back to echoing its own title. Strip after choosing.
  const lines = hitLines(row.doc.body, props.query, 12)
  const body = lines.find(h => !HEADING_RE.test(h.text)) || lines[0]
  return body ? plainLine(body.text, !!row.doc.mono) : ''
}

// A row whose only match is in its title still says something useful
// underneath — the sub line is the fallback, not an empty snippet.
const rowSnippet = (row) => {
  const line = row.hits ? bestLine(row) : ''
  return snippet(line || row.doc.sub, props.query)
}

// A path's filename is the identifying half and it sits at the END, which is
// exactly what right-truncation eats. Split it so the directory can shrink
// and the name cannot.
const SLASH = /^(.*\/)([^/]+)$/
const rowPath = (row) => {
  if (row.doc.kind !== 'file') return { dir: '', name: row.doc.title }
  const m = String(row.doc.title || '').match(SLASH)
  return m ? { dir: m[1], name: m[2] } : { dir: '', name: row.doc.title }
}

// The busiest row in the list sets the bar's full width — this is a relative
// signal ("which of these is densest"), not an absolute one.
const peak = computed(() =>
  Math.max(1, ...props.groups.flatMap(g => g.rows.map(r => r.hits || 0))))
const hitWeight = row => `${Math.round(((row.hits || 0) / peak.value) * 100)}%`

const titleSnippet = row => snippet(row.doc.title, props.query, 24, 96)

// "matches", not "hits": the preview header counts the same number and says
// matches, and one quantity under two names in one pane is exactly the kind
// of thing the redesign is meant to remove.
const hitLabel = (row) => {
  if (!row.hits) return ''
  return row.hits === 1 ? '1 match' : `${row.hits} matches`
}

const overflow = (group) => {
  const cap = group.kind === 'span' ? SPAN_ROW_CAP : OTHER_ROW_CAP
  return group.total > cap ? group.total - cap : 0
}

const SHIMMER_ROWS = 3
</script>

<template>
  <div class="search-results content-scroll">
    <!-- With the taxonomy moved into the rail this column is the only thing
         left with nothing in it before the first keystroke, and a blank pane
         beside a populated rail reads as a failed load rather than as a
         waiting one. -->
    <p v-if="held && !groups.length" class="await-query">
      Type {{ MIN_QUERY_CHARS }} characters to search.
    </p>

    <!-- Narrowing to one session is a filter the reader chose, so an empty
         result under it is a dead end unless widening is offered right here
         rather than as another guess at where the control lives. -->
    <div v-if="noMatches" class="no-matches">
      <p class="no-matches-title">No matches</p>
      <p v-if="canWiden" class="no-matches-body">
        Nothing in this session matches. Span bodies, diffs, shell output and
        prose are all indexed, so the term may simply live elsewhere.
      </p>
      <p v-else class="no-matches-body">
        Wiki pages, other sessions and touched files are all indexed. Try a
        shorter term.
      </p>
      <button
        v-if="canWiden"
        type="button"
        class="no-matches-widen focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        @click="$emit('widen')"
      >Search every session</button>
    </div>

    <section
      v-for="group in groups"
      :key="group.kind"
      role="group"
      :aria-label="meta(group.kind).label"
    >
      <header class="group-head">
        <Icon
          class="group-glyph"
          :name="meta(group.kind).icon"
          :size="12"
          :style="{ color: meta(group.kind).accent }"
        />
        <span class="group-label">{{ meta(group.kind).label }}</span>
        <span
          class="group-count"
          :style="{ '--accent': meta(group.kind).accent }"
        >{{ group.total }}</span>
        <span class="group-rule" />
        <button
          v-if="group.failed"
          type="button"
          class="group-retry focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          @click="$emit('retry')"
        >{{ group.rows.length ? 'showing loaded spans only · retry' : "couldn't reach the index · retry" }}</button>
      </header>

      <div v-if="group.pending" class="shimmer-wrap">
        <div v-for="n in SHIMMER_ROWS" :key="n" class="shimmer" />
      </div>

      <div
        v-for="row in group.rows"
        :key="row.doc.id"
        role="option"
        :id="`search-row-${row.doc.id}`"
        :aria-selected="row.doc.id === activeId"
        class="result-row cursor-pointer hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        :class="{ 'is-active': row.doc.id === activeId }"
        @click="$emit('select', row)"
        @dblclick="$emit('open', row)"
      >
        <Icon
          class="row-glyph"
          :name="meta(group.kind).icon"
          :size="14"
          :style="{ color: meta(group.kind).accent }"
        />
        <span class="row-main">
          <span class="row-title">
            <span v-if="rowPath(row).dir" class="row-dir">{{ rowPath(row).dir }}</span>
            <SearchMark class="row-name" :part="titleSnippet({ doc: { title: rowPath(row).name } })" />
          </span>
          <SearchMark class="row-sub" :part="rowSnippet(row)" tone="soft" />
        </span>
        <span class="row-meta">
          <span v-if="hitLabel(row)" class="row-hits">
            <span class="row-bar" aria-hidden="true"><i :style="{ width: hitWeight(row) }" /></span>
            {{ hitLabel(row) }}
          </span>
          <SearchRowTag v-if="row.doc.tag" :kind="group.kind" :tag="row.doc.tag" />
          <span v-if="row.doc.id === activeId" class="row-enter">↵</span>
        </span>
      </div>

      <p v-if="overflow(group)" class="group-overflow">+{{ overflow(group) }} more</p>
    </section>
  </div>
</template>

<style scoped>
.search-results {
  width: 46%;
  min-width: 320px;
  overflow-y: auto;
  padding: 0.5rem 0.625rem 1rem;
  border-right: 1px solid var(--color-border);
}

.await-query {
  margin: 0;
  padding: 2.75rem 1.25rem;
  text-align: center;
  font-size: var(--sp-t-meta);
  color: var(--color-fg-faint);
}

.no-matches { padding: 2.75rem 1.25rem; text-align: center; }

.no-matches-title {
  font-size: var(--sp-t-body);
  font-weight: 600;
  color: var(--color-fg);
  margin: 0;
}

.no-matches-body {
  font-size: var(--sp-t-meta);
  color: var(--color-fg-subtle);
  line-height: 1.6;
  margin: 0.375rem auto 0;
  max-width: 24rem;
}

.no-matches-widen {
  margin-top: 0.875rem;
  font-size: var(--sp-t-meta);
  font-weight: 600;
  color: var(--color-info-strong);
  background: var(--color-info-soft);
  border: 1px solid transparent;
  border-radius: 9px;
  padding: 0.375rem 0.75rem;
  cursor: pointer;
}

.no-matches-widen:hover { border-color: var(--color-info); }

.group-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.875rem 0.75rem 0.375rem;
}

.group-label {
  font-size: var(--sp-t-micro);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--color-fg-subtle);
  font-weight: 700;
  white-space: nowrap;
  margin: 0;
}

.group-glyph { flex-shrink: 0; opacity: 0.9; }

.group-count {
  font-size: var(--sp-t-micro);
  font-weight: 700;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border-radius: 6px;
  padding: 1px 6px;
}

.group-rule { flex: 1; height: 1px; background: var(--color-border); }

.group-retry {
  font-size: var(--sp-t-micro);
  color: var(--color-danger);
  background: none;
  border: 0;
  cursor: pointer;
  white-space: nowrap;
}

.shimmer-wrap { padding: 0.25rem 0.75rem; display: grid; gap: 0.5rem; }

.shimmer {
  height: 2.25rem;
  border-radius: 11px;
  background: linear-gradient(90deg,
    var(--color-surface-2) 25%, var(--color-background) 50%, var(--color-surface-2) 75%);
  background-size: 200% 100%;
  animation: search-shimmer 1.2s ease-in-out infinite;
}

@keyframes search-shimmer {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}

@media (prefers-reduced-motion: reduce) {
  .shimmer { animation: none; }
}

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

.row-enter { font-size: var(--sp-t-micro); color: var(--color-primary); }

.group-overflow {
  font-size: var(--sp-t-micro);
  color: var(--color-fg-subtle);
  padding: 0.25rem 0.75rem 0;
  margin: 0;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
}

@media (max-width: 900px) {
  /* min-width has to go with the width: as the only pane it is no longer
     competing with the preview for room, and a 320px floor is wider than the
     panel itself on a small phone — which overflowed the whole modal. */
  .search-results { width: 100%; min-width: 0; border-right: 0; }
  .row-sub { white-space: normal; overflow: visible; }
}
</style>
