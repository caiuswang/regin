<script setup>
// One-row toolbar: what to match (search + scope), how to arrange (grouping),
// and how to narrow (range + the filters popover). Everything that narrows the
// result set lives behind `Filters` so the resting state stays a single line.
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import SessionFiltersPopover from './SessionFiltersPopover.vue'
import SessionRangePicker from './SessionRangePicker.vue'
import { GROUP_OPTIONS } from '../../composables/useSessionGrouping.js'

defineProps({
  scopeOptions: { type: Array, required: true },
  rangeOptions: { type: Array, required: true },
  rangeLabel: { type: String, default: '' },
  rangeNarrowed: { type: Boolean, default: false },
  rangeStart: { type: Object, default: null },
  rangeEnd: { type: Object, default: null },
  kindOptions: { type: Array, required: true },
  statusOptions: { type: Array, required: true },
  tagOptions: { type: Array, default: () => [] },
  repoOptions: { type: Array, default: () => [] },
  repoCounts: { type: Object, default: () => ({}) },
  filterCount: { type: Number, default: 0 },
  selectionCount: { type: Number, default: 0 },
  batchDeleting: { type: Boolean, default: false },
})

const emit = defineEmits([
  'search', 'reset', 'batch-delete', 'clear-selection',
  'range-preset', 'range-pick', 'range-clear',
])

const search = defineModel('search', { type: String, default: '' })
const scope = defineModel('scope', { type: String, default: 'title' })
const group = defineModel('group', { type: String, default: 'active' })
const range = defineModel('range', { type: String, default: 'today' })
const kind = defineModel('kind', { type: String, default: 'real' })
const status = defineModel('status', { type: String, default: 'all' })
const tag = defineModel('tag', { type: String, default: '' })
const repo = defineModel('repo', { type: String, default: 'all' })
const traceId = defineModel('traceId', { type: String, default: '' })
</script>

<template>
  <div class="stoolbar">
    <div class="stoolbar__search">
      <Input
        v-model="search"
        type="search"
        class="stoolbar__input focus-visible:outline-2 focus-visible:outline-blue-500"
        placeholder="Search sessions…"
        aria-label="Search sessions"
        @keyup.enter="emit('search')"
      />
      <Select
        v-model="scope"
        :options="scopeOptions"
        class="stoolbar__scope"
        aria-label="What to search"
        title="What `Search` matches against"
      />
    </div>

    <div class="segmented stoolbar__group" role="group" aria-label="Group sessions by">
      <Button
        v-for="opt in GROUP_OPTIONS"
        :key="opt.value"
        variant="ghost"
        size="sm"
        class="segmented-item focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-active': group === opt.value }"
        :aria-pressed="group === opt.value"
        @click="group = opt.value"
      >{{ opt.label }}</Button>
    </div>

    <SessionRangePicker
      :presets="rangeOptions"
      :value="range"
      :label="rangeLabel"
      :narrowed="rangeNarrowed"
      :start="rangeStart"
      :end="rangeEnd"
      @preset="(v) => emit('range-preset', v)"
      @pick="(d) => emit('range-pick', d)"
      @clear="emit('range-clear')"
    />

    <SessionFiltersPopover
      v-model:kind="kind"
      v-model:status="status"
      v-model:tag="tag"
      v-model:repo="repo"
      v-model:trace-id="traceId"
      :kind-options="kindOptions"
      :status-options="statusOptions"
      :tag-options="tagOptions"
      :repo-options="repoOptions"
      :repo-counts="repoCounts"
      :active-count="filterCount"
      @reset="emit('reset')"
      @commit-trace-id="emit('search')"
    />

    <div v-if="selectionCount" class="stoolbar__batch" role="group" aria-label="Selected sessions">
      <span class="stoolbar__selcount">{{ selectionCount }} selected</span>
      <Button
        variant="ghost"
        size="sm"
        class="stoolbar__clearsel focus-visible:outline-2 focus-visible:outline-blue-500"
        :disabled="batchDeleting"
        @click="emit('clear-selection')"
      >Clear</Button>
      <Button
        variant="danger"
        size="sm"
        :disabled="batchDeleting"
        @click="emit('batch-delete')"
      >{{ batchDeleting ? 'Deleting…' : 'Delete selected' }}</Button>
    </div>
  </div>
</template>

<style scoped>
.stoolbar {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.stoolbar__search {
  align-items: stretch;
  display: inline-flex;
  max-width: 100%;
  min-width: 0;
}
.stoolbar__search :deep(.stoolbar__input) {
  border-bottom-right-radius: 0;
  border-right: 0;
  border-top-right-radius: 0;
  height: 2.125rem;
  min-width: 0;
  width: 22rem;
}
/* :deep() — the class lands on Select's trigger (a child component root),
   out of reach of a plain scoped selector. */
.stoolbar__search :deep(.stoolbar__scope) {
  background: var(--color-surface-2);
  border-bottom-left-radius: 0;
  border-left: 1px solid var(--color-border);
  border-top-left-radius: 0;
  color: var(--color-fg-muted);
  font-size: 0.78125rem;
  height: 2.125rem;
  min-width: 0;
}
.stoolbar__group { padding: 0.1875rem; gap: 0.125rem; }
.stoolbar__group :deep(.segmented-item) {
  font-size: 0.75rem;
  height: 1.75rem;
  padding: 0 0.6875rem;
}
.stoolbar__batch {
  align-items: center;
  display: inline-flex;
  gap: 0.375rem;
  margin-left: auto;
}
.stoolbar__selcount {
  color: var(--color-fg-muted);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.stoolbar__clearsel { color: var(--color-fg-faint); font-size: 0.6875rem; }

@media (max-width: 639px) {
  .stoolbar__search { width: 100%; }
  .stoolbar__search :deep(.stoolbar__input) { width: 100%; }
  .stoolbar__group { order: 3; width: 100%; }
}
</style>
