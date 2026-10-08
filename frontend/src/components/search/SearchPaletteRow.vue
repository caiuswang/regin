<script setup>
// One `PaletteRow` (lib/palette/rows.py) in two lines and never more:
//   ref · title · HERE · owner chips
//   status word · context · aside
// Each line clips on its own, so a long prompt cannot push the row to three.
import SearchMark from './SearchMark.vue'
import { snippet } from '../../utils/searchDocs.js'

defineProps({
  row: { type: Object, required: true },
  query: { type: String, default: '' },
})
</script>

<template>
  <span class="palette-row">
    <span class="pr-line">
      <span class="pr-dot" :data-bucket="row.status?.bucket" aria-hidden="true" />
      <span class="pr-ref">{{ row.ref }}</span>
      <SearchMark class="pr-title" :part="snippet(row.title, query, 24, 96)" />
      <span v-if="row.here" class="pr-here">HERE</span>
      <span v-if="row.owners?.length" class="pr-owners">
        <span
          v-for="chip in row.owners"
          :key="chip.ref"
          class="pr-chip"
          :class="{ 'is-none': chip.kind === 'none' }"
          :title="chip.title || undefined"
        >{{ chip.ref }}</span>
      </span>
    </span>
    <span class="pr-line is-sub">
      <span class="pr-status">{{ row.status?.word }}</span>
      <SearchMark class="pr-context" :part="snippet(row.context || '', query)" tone="soft" />
      <span v-if="row.aside" class="pr-aside">{{ row.aside }}</span>
    </span>
  </span>
</template>

<style scoped>
.palette-row { flex: 1; min-width: 0; display: block; }

.pr-line {
  display: flex;
  align-items: center;
  gap: 0.4375rem;
  min-width: 0;
  white-space: nowrap;
  font-size: var(--sp-t-body);
  line-height: 1.38;
}

.pr-line.is-sub {
  margin-top: 3px;
  padding-left: 0.875rem;
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  color: var(--color-fg-subtle);
}

.pr-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--color-fg-faint);
}

.pr-dot[data-bucket='needs_you'] { background: var(--color-warning); }
.pr-dot[data-bucket='active'] { background: var(--color-success); }
.pr-dot[data-bucket='done'] { background: var(--color-info); }

.pr-ref {
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.93em;
  font-weight: 600;
  color: var(--color-fg-muted);
}

.pr-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 550;
  color: var(--color-fg);
}

.pr-here {
  flex-shrink: 0;
  font-size: var(--sp-t-kbd);
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--color-primary-fg);
  background: var(--color-primary);
  border-radius: 4px;
  padding: 1px 5px;
}

.pr-owners { flex-shrink: 0; display: inline-flex; gap: 0.25rem; }

.pr-chip {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-kbd);
  font-weight: 600;
  color: var(--color-fg-muted);
  border: 1px solid var(--color-border);
  border-radius: 5px;
  padding: 1px 5px;
  background: var(--color-surface);
}

.pr-chip.is-none { font-family: inherit; font-style: italic; color: var(--color-fg-faint); }

.pr-status { flex-shrink: 0; font-weight: 650; color: var(--color-fg-muted); }

.pr-context { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }

.pr-aside {
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  color: var(--color-fg-faint);
}
</style>
