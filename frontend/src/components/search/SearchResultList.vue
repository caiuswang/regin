<script setup>
import Icon from '../ui/Icon.vue'
import SearchMark from './SearchMark.vue'
import SearchRowTag from './SearchRowTag.vue'
import {
  KIND_META, OTHER_ROW_CAP, SPAN_ROW_CAP, snippet,
} from '../../utils/searchDocs.js'

const props = defineProps({
  groups: { type: Array, default: () => [] },
  query: { type: String, default: '' },
  activeId: { type: String, default: null },
  recents: { type: Array, default: () => [] },
  showRecents: { type: Boolean, default: false },
  noMatches: { type: Boolean, default: false },
  canWiden: { type: Boolean, default: false },
})
defineEmits(['select', 'open', 'retry', 'recent', 'widen'])

const meta = kind => KIND_META[kind] || KIND_META.span

// Source is the body when it holds hits, else the sub line — a row whose
// only match is in its title still says something useful underneath.
const rowSnippet = row =>
  snippet(row.hits ? row.doc.body : row.doc.sub, props.query)

const titleSnippet = row => snippet(row.doc.title, props.query, 24, 96)

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
    <div v-if="showRecents && recents.length" class="recents">
      <p class="group-label">Recent searches</p>
      <div class="recent-chips">
        <button
          v-for="term in recents"
          :key="term"
          type="button"
          class="recent-chip"
          @click="$emit('recent', term)"
        >{{ term }}</button>
      </div>
    </div>

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
        class="no-matches-widen"
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
          class="group-retry"
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
        class="result-row"
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
          <SearchMark class="row-title" :part="titleSnippet(row)" />
          <SearchMark class="row-sub" :part="rowSnippet(row)" tone="soft" />
        </span>
        <span class="row-meta">
          <span v-if="hitLabel(row)" class="row-hits">{{ hitLabel(row) }}</span>
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

.recents { padding: 0.75rem 0.625rem 0.25rem; }

.recent-chips { display: flex; flex-wrap: wrap; gap: 0.4375rem; margin-top: 0.5rem; }

.recent-chip {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-fg-muted);
  background: var(--color-background);
  border: 1px solid var(--color-border);
  border-radius: 9px;
  padding: 0.3125rem 0.625rem;
  cursor: pointer;
  white-space: nowrap;
}

.recent-chip:hover { border-color: var(--color-fg-subtle); color: var(--color-fg); }

.no-matches { padding: 2.75rem 1.25rem; text-align: center; }

.no-matches-title {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0;
}

.no-matches-body {
  font-size: 0.78125rem;
  color: var(--color-fg-subtle);
  line-height: 1.6;
  margin: 0.375rem auto 0;
  max-width: 24rem;
}

.no-matches-widen {
  margin-top: 0.875rem;
  font-size: 0.75rem;
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
  font-size: 0.59375rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--color-fg-subtle);
  font-weight: 700;
  white-space: nowrap;
  margin: 0;
}

.group-glyph { flex-shrink: 0; opacity: 0.9; }

.group-count {
  font-size: 0.625rem;
  font-weight: 700;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border-radius: 6px;
  padding: 1px 6px;
}

.group-rule { flex: 1; height: 1px; background: var(--color-border); }

.group-retry {
  font-size: 0.6875rem;
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

.result-row.is-active { background: var(--color-info-soft); }

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

.row-title,
.row-sub {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-title {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-fg);
}

.result-row.is-active .row-title { color: var(--color-info-strong); }

.row-sub {
  font-size: 0.71875rem;
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

.row-hits {
  font-size: 0.625rem;
  font-weight: 600;
  color: var(--search-hit-soft-fg);
  background: var(--search-hit-soft-bg);
  border-radius: 6px;
  padding: 1px 6px;
  white-space: nowrap;
}

.row-enter { font-size: 0.75rem; color: var(--color-info); }

.group-overflow {
  font-size: 0.6875rem;
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
