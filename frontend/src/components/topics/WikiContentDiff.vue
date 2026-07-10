<script setup>
/**
 * WikiContentDiff — a word-level diff of two wiki markdown bodies, shown
 * either as a Unified line diff (line rows, but changed lines highlight
 * only the words that moved) or as an Inline flowing block.
 *
 * Presentational only: the parent supplies before/after strings (from the
 * server `/diff` response, or two fetched revisions). Rendering the diff
 * client-side is safe because the STRINGS come from the server; we never
 * recompute the graph decision here.
 */
import { computed, ref } from 'vue'
import { lineDiff, diffStats } from '../../utils/lineDiff'
import { annotateRowSegments, wordSegments, wordStats } from '../../utils/wordDiff'
import Button from '../ui/Button.vue'

const props = defineProps({
  before: { type: String, default: '' },
  after: { type: String, default: '' },
  beforeLabel: { type: String, default: 'Before' },
  afterLabel: { type: String, default: 'After' },
})

const view = ref('unified')
const expanded = ref(false)

const rows = computed(() => lineDiff(props.before, props.after))
const stats = computed(() => diffStats(rows.value))
const annotatedRows = computed(() => annotateRowSegments(rows.value))
const inlineSegments = computed(() => wordSegments(props.before, props.after))
const inlineStats = computed(() => wordStats(inlineSegments.value))
const isIdentical = computed(() => stats.value.added === 0 && stats.value.removed === 0)
const isNew = computed(() => !props.before.trim() && !!props.after.trim())
const isEmpty = computed(() => !props.before.trim() && !props.after.trim())
const hasDiff = computed(() => !isEmpty.value && !isIdentical.value)
</script>

<template>
  <div class="wikidiff" data-testid="wiki-content-diff">
    <header class="wikidiff__head">
      <span class="wikidiff__title">{{ beforeLabel }} → {{ afterLabel }}</span>
      <span v-if="isNew" class="wikidiff__badge wikidiff__badge--new">New wiki</span>
      <template v-else>
        <span v-if="stats.added" class="wikidiff__badge wikidiff__badge--add">+{{ stats.added }}</span>
        <span v-if="stats.removed" class="wikidiff__badge wikidiff__badge--remove">−{{ stats.removed }}</span>
      </template>
      <div
        v-if="hasDiff"
        class="wikidiff__toggle"
        role="radiogroup"
        aria-label="Diff view"
        data-testid="wiki-diff-view-toggle"
      >
        <Button
          v-for="opt in [{ key: 'unified', label: 'Unified' }, { key: 'inline', label: 'Inline' }]"
          :key="opt.key"
          variant="ghost"
          size="sm"
          role="radio"
          :aria-checked="view === opt.key"
          :class="['wikidiff__toggle-btn', { 'wikidiff__toggle-btn--active': view === opt.key }]"
          :data-testid="`wiki-diff-view-${opt.key}`"
          @click="view = opt.key"
        >{{ opt.label }}</Button>
      </div>
    </header>

    <p v-if="isEmpty" class="wikidiff__empty" data-testid="wiki-diff-empty">
      No wiki content on either side.
    </p>
    <p v-else-if="isIdentical" class="wikidiff__empty" data-testid="wiki-diff-identical">
      No content changes — the wiki body is unchanged.
    </p>

    <div
      v-else-if="view === 'unified'"
      class="wikidiff__body"
      :class="{ 'wikidiff__body--expanded': expanded }"
      role="table"
      aria-label="Wiki content diff"
    >
      <div
        v-for="(row, i) in rows"
        :key="i"
        class="wikidiff__row"
        :class="`wikidiff__row--${row.type}`"
        role="row"
      >
        <span class="wikidiff__gutter" aria-hidden="true">{{ row.beforeLine ?? '' }}</span>
        <span class="wikidiff__gutter" aria-hidden="true">{{ row.afterLine ?? '' }}</span>
        <span class="wikidiff__sign" aria-hidden="true">
          {{ row.type === 'add' ? '+' : row.type === 'remove' ? '−' : '' }}
        </span>
        <span class="wikidiff__text"><span
          v-for="(seg, s) in annotatedRows[i].segments"
          :key="s"
          :class="seg.type === 'context' ? '' : `wikidiff__seg wikidiff__seg--${seg.type}`"
        >{{ seg.text }}</span><template v-if="!row.text"> </template></span>
      </div>
    </div>

    <div v-else class="wikidiff__inline-wrap" data-testid="wiki-diff-inline">
      <div class="wikidiff__stats" data-testid="wiki-diff-stats">
        <span class="wikidiff__stat">Common: <strong>{{ inlineStats.common }}</strong></span>
        <span class="wikidiff__stat wikidiff__stat--add">New: <strong>{{ inlineStats.added }}</strong></span>
        <span class="wikidiff__stat wikidiff__stat--remove">Removed: <strong>{{ inlineStats.removed }}</strong></span>
      </div>
      <p class="wikidiff__inline" :class="{ 'wikidiff__body--expanded': expanded }">
        <span
          v-for="(seg, s) in inlineSegments"
          :key="s"
          :class="seg.type === 'context' ? '' : `wikidiff__seg wikidiff__seg--${seg.type}`"
        >{{ seg.text }}</span>
      </p>
    </div>

    <Button
      v-if="hasDiff"
      variant="ghost"
      size="sm"
      class="wikidiff__expand min-h-9 w-full rounded-none"
      :aria-expanded="expanded"
      data-testid="wiki-diff-expand"
      @click="expanded = !expanded"
    >{{ expanded ? 'Collapse diff' : 'Show full diff' }}</Button>
  </div>
</template>

<style scoped>
.wikidiff {
  border: 1px solid var(--color-slate-200);
  border-radius: 0.625rem;
  overflow: hidden;
  background: var(--color-white);
}
.wikidiff__head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: var(--color-slate-50);
  border-bottom: 1px solid var(--color-slate-200);
  font-size: 0.75rem;
}
.wikidiff__title {
  font-weight: 600;
  color: var(--color-slate-600);
}
.wikidiff__badge {
  font-size: 0.6875rem;
  font-weight: 600;
  padding: 0.0625rem 0.375rem;
  border-radius: 999px;
}
.wikidiff__badge--add { background: var(--color-green-100); color: var(--color-green-700); }
.wikidiff__badge--remove { background: var(--color-red-100); color: var(--color-red-700); }
.wikidiff__badge--new { background: var(--color-emerald-50); color: var(--color-emerald-700); }

.wikidiff__empty {
  padding: 0.75rem;
  margin: 0;
  font-size: 0.75rem;
  font-style: italic;
  color: var(--color-slate-400);
}

.wikidiff__body {
  max-height: 28rem;
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.75rem;
  line-height: 1.5;
}
.wikidiff__row {
  display: grid;
  grid-template-columns: 2.5rem 2.5rem 1rem 1fr;
  align-items: baseline;
  column-gap: 0.25rem;
  padding: 0 0.5rem;
  white-space: pre-wrap;
  word-break: break-word;
}
.wikidiff__row--add { background: var(--color-emerald-50); }
.wikidiff__row--remove { background: var(--color-red-50); }
.wikidiff__gutter {
  text-align: right;
  color: var(--color-slate-400);
  user-select: none;
}
.wikidiff__sign {
  text-align: center;
  font-weight: 700;
}
.wikidiff__row--add .wikidiff__sign { color: var(--color-green-700); }
.wikidiff__row--remove .wikidiff__sign { color: var(--color-red-700); }
.wikidiff__text { color: var(--color-slate-800); }
.wikidiff__row--add .wikidiff__text { color: var(--color-emerald-800); }
.wikidiff__row--remove .wikidiff__text { color: var(--color-red-800); }

/* ── Word-level highlight (shared by both views) ─────────────────── */
.wikidiff__seg { border-radius: 0.1875rem; padding: 0 0.0625rem; }
.wikidiff__seg--add {
  background: var(--color-emerald-200);
  color: var(--color-emerald-900);
}
.wikidiff__seg--remove {
  background: var(--color-red-200);
  color: var(--color-red-900);
  text-decoration: line-through;
}

/* ── View toggle ────────────────────────────────────────────────── */
.wikidiff__toggle {
  margin-left: auto;
  display: inline-flex;
  padding: 0.125rem;
  gap: 0.125rem;
  background: var(--color-slate-100);
  border-radius: 0.5rem;
}
.wikidiff__toggle-btn {
  height: 2.25rem;
  padding: 0 0.5rem;
  font-size: 0.6875rem;
  font-weight: 600;
  color: var(--color-slate-500);
  border-radius: 0.375rem;
}
.wikidiff__toggle-btn:hover:not(.wikidiff__toggle-btn--active) {
  background: transparent;
  color: var(--color-slate-800);
}
.wikidiff__toggle-btn--active {
  background: var(--color-white);
  color: var(--color-slate-900);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}

/* ── Inline flowing view ────────────────────────────────────────── */
.wikidiff__stats {
  display: flex;
  flex-wrap: wrap;
  gap: 0.875rem;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-slate-100);
  font-size: 0.6875rem;
  color: var(--color-slate-500);
}
.wikidiff__stat strong { color: var(--color-slate-800); font-weight: 600; }
.wikidiff__stat--add strong { color: var(--color-emerald-700); }
.wikidiff__stat--remove strong { color: var(--color-red-700); }
.wikidiff__inline {
  margin: 0;
  padding: 0.75rem;
  max-height: 28rem;
  overflow: auto;
  font-size: 0.8125rem;
  line-height: 1.6;
  color: var(--color-slate-800);
  white-space: pre-wrap;
  word-break: break-word;
}
.wikidiff__body--expanded {
  max-height: none;
}
.wikidiff__expand {
  border-top: 1px solid var(--color-slate-100);
  color: var(--color-slate-500);
}
</style>
