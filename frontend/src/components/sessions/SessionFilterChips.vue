<script setup>
// One facet row inside the filters popover: an "All" chip plus one chip per
// option, each carrying its count for the current filter set. Long facets
// (repos, tags) fold behind a "+N more" toggle so the popover keeps a stable
// height until the user asks for the tail.
import { computed, ref } from 'vue'
import Button from '../ui/Button.vue'

const props = defineProps({
  label: { type: String, required: true },
  // [{ value, label, count }] — `value` '' / 'all' is supplied by the caller.
  options: { type: Array, default: () => [] },
  allValue: { type: String, default: '' },
  allLabel: { type: String, default: 'All' },
  visible: { type: Number, default: 3 },
})

const model = defineModel({ type: String, default: '' })
const expanded = ref(false)

const rest = computed(() => props.options.filter(o => o.value !== props.allValue))
const shown = computed(() => (expanded.value ? rest.value : rest.value.slice(0, props.visible)))
const hiddenCount = computed(() => rest.value.length - shown.value.length)
</script>

<template>
  <div class="facet">
    <div class="facet__label">{{ label }}</div>
    <div class="facet__chips">
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
      <Button
        v-if="hiddenCount > 0 || expanded"
        variant="ghost"
        size="sm"
        class="chip chip--more focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="expanded = !expanded"
      >{{ expanded ? 'Show less' : `+${hiddenCount} more` }}</Button>
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
.facet__chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  min-width: 0;
}
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
