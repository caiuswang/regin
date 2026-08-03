<script setup>
// One facet row inside the filters popover: an "All" chip plus one chip per
// option, each carrying its count for the current filter set. Long facets
// (repos, tags) fold behind a "+N more" toggle so the popover keeps a stable
// height until the user asks for the tail.
import { computed, ref } from 'vue'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'

const props = defineProps({
  label: { type: String, required: true },
  // [{ value, label, count }] — `value` '' / 'all' is supplied by the caller.
  options: { type: Array, default: () => [] },
  allValue: { type: String, default: '' },
  allLabel: { type: String, default: 'All' },
  visible: { type: Number, default: 3 },
  // Placeholder for the filter box shown once a long facet is expanded;
  // absent means the facet is short enough to scan and needs none.
  searchPlaceholder: { type: String, default: '' },
})

const model = defineModel({ type: String, default: '' })
const expanded = ref(false)
const term = ref('')

// The caller orders by count, but the selected chip has to stay reachable —
// otherwise picking a 0-count repo, then reopening the popover, hides the
// very filter that is narrowing the list behind "+N more".
const rest = computed(() => props.options
  .filter(o => o.value !== props.allValue)
  .sort((a, b) => (b.value === model.value) - (a.value === model.value)))

const matching = computed(() => {
  const q = term.value.trim().toLowerCase()
  if (!expanded.value || !q) return rest.value
  return rest.value.filter(o => o.label.toLowerCase().includes(q))
})

const shown = computed(() => (expanded.value ? matching.value : rest.value.slice(0, props.visible)))
const hiddenCount = computed(() => rest.value.length - shown.value.length)
const searchable = computed(() =>
  Boolean(props.searchPlaceholder) && expanded.value && rest.value.length > props.visible)

function toggleExpanded() {
  expanded.value = !expanded.value
  term.value = ''
}
</script>

<template>
  <div class="facet">
    <div class="facet__label">{{ label }}</div>
    <div class="facet__body">
      <Input
        v-if="searchable"
        v-model="term"
        type="search"
        class="facet__search focus-visible:outline-2 focus-visible:outline-blue-500"
        :placeholder="searchPlaceholder"
        :aria-label="`Search ${label.toLowerCase()} options`"
      />
      <div class="facet__chips" :class="{ 'facet__chips--scroll': expanded }">
      <Button
        variant="ghost"
        size="sm"
        class="chip focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'chip--on': model === allValue }"
        :aria-pressed="model === allValue"
        @click="model = allValue"
      >{{ allLabel }}</Button>
      <Button
        v-for="opt in shown"
        :key="opt.value"
        variant="ghost"
        size="sm"
        class="chip focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'chip--on': model === opt.value }"
        :aria-pressed="model === opt.value"
        @click="model = opt.value"
      >
        {{ opt.label }}
        <span v-if="opt.count != null" class="chip__count">· {{ opt.count }}</span>
      </Button>
      <span v-if="expanded && !shown.length" class="facet__empty">No match</span>
      <Button
        v-if="hiddenCount > 0 || expanded"
        variant="ghost"
        size="sm"
        class="chip chip--more focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="toggleExpanded"
      >{{ expanded ? 'Show less' : `+${hiddenCount} more` }}</Button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.facet {
  align-items: baseline;
  display: grid;
  gap: 0.5rem;
  grid-template-columns: 3.75rem 1fr;
}
.facet__label {
  color: var(--color-fg-faint);
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.facet__body { display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; }
.facet__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  min-width: 0;
}
/* Expanding a long facet must not push the popover past the viewport — cap it
   and scroll, so Trace ID and Reset all stay reachable. */
.facet__chips--scroll { max-height: 6.5rem; overflow-y: auto; }
.facet__search {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 0.5rem;
  color: var(--color-fg);
  font-size: 0.75rem;
  padding: 0.25rem 0.5rem;
  width: 100%;
}
.facet__search::placeholder { color: var(--color-fg-faint); }
.facet__empty { color: var(--color-fg-faint); font-size: 0.75rem; padding: 0.125rem 0; }
.chip {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 9999px;
  color: var(--color-fg-muted);
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 500;
  line-height: 1.5;
  max-width: 100%;
  overflow: hidden;
  padding: 0.125rem 0.625rem;
  text-overflow: ellipsis;
  transition: border-color 0.12s ease, background 0.12s ease, color 0.12s ease;
  white-space: nowrap;
}
.chip:hover { border-color: var(--color-border-strong); color: var(--color-fg); }
.chip--on {
  background: var(--color-primary);
  border-color: var(--color-primary);
  color: var(--color-primary-fg);
  font-weight: 600;
}
.chip--on:hover { color: var(--color-primary-fg); }
.chip__count { font-variant-numeric: tabular-nums; opacity: 0.7; }
.chip--more { border-style: dashed; color: var(--color-fg-subtle); }
</style>
