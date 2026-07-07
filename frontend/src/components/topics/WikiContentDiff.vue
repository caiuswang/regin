<script setup>
/**
 * WikiContentDiff — a unified line diff of two wiki markdown bodies.
 *
 * Presentational only: the parent supplies before/after strings (from the
 * server `/diff` response, or two fetched revisions). Rendering the diff
 * client-side is safe because the STRINGS come from the server; we never
 * recompute the graph decision here.
 */
import { computed } from 'vue'
import { lineDiff, diffStats } from '../../utils/lineDiff'

const props = defineProps({
  before: { type: String, default: '' },
  after: { type: String, default: '' },
  beforeLabel: { type: String, default: 'Before' },
  afterLabel: { type: String, default: 'After' },
})

const rows = computed(() => lineDiff(props.before, props.after))
const stats = computed(() => diffStats(rows.value))
const isIdentical = computed(() => stats.value.added === 0 && stats.value.removed === 0)
const isNew = computed(() => !props.before.trim() && !!props.after.trim())
const isEmpty = computed(() => !props.before.trim() && !props.after.trim())
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
    </header>

    <p v-if="isEmpty" class="wikidiff__empty" data-testid="wiki-diff-empty">
      No wiki content on either side.
    </p>
    <p v-else-if="isIdentical" class="wikidiff__empty" data-testid="wiki-diff-identical">
      No content changes — the wiki body is unchanged.
    </p>

    <div v-else class="wikidiff__body" role="table" aria-label="Wiki content diff">
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
        <span class="wikidiff__text">{{ row.text || ' ' }}</span>
      </div>
    </div>
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
</style>
