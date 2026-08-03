<script setup>
// What is currently narrowing the list, spelled out. A count badge on the
// Filters trigger says HOW MANY axes are on; only this row says WHICH — and
// gives each one its own dismiss, so undoing a single facet doesn't mean
// reopening the popover to hunt for it.
import Button from '../ui/Button.vue'

defineProps({
  // [{ key, label }] — one entry per axis off its default.
  filters: { type: Array, default: () => [] },
})

const emit = defineEmits(['clear', 'clear-all'])
</script>

<template>
  <div v-if="filters.length" class="afilters" role="group" aria-label="Active filters">
    <span class="afilters__label">Filtered by</span>
    <span v-for="f in filters" :key="f.key" class="afilter">
      <span class="afilter__text">{{ f.label }}</span>
      <Button
        variant="ghost"
        size="sm"
        class="afilter__x focus-visible:outline-2 focus-visible:outline-blue-500"
        :aria-label="`Clear filter: ${f.label}`"
        :title="`Clear filter: ${f.label}`"
        @click="emit('clear', f.key)"
      >×</Button>
    </span>
    <Button
      v-if="filters.length > 1"
      variant="ghost"
      size="sm"
      class="afilters__reset focus-visible:outline-2 focus-visible:outline-blue-500"
      @click="emit('clear-all')"
    >Clear all</Button>
  </div>
</template>

<style scoped>
.afilters {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  margin-top: 0.5rem;
}
.afilters__label {
  color: var(--color-fg-faint);
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  margin-right: 0.125rem;
  text-transform: uppercase;
}
.afilter {
  align-items: center;
  background: var(--color-blue-50);
  border: 1px solid var(--color-primary);
  border-radius: 9999px;
  color: var(--color-blue-800);
  display: inline-flex;
  font-size: 0.75rem;
  font-weight: 600;
  gap: 0.125rem;
  max-width: 100%;
  min-width: 0;
  padding: 0.0625rem 0.1875rem 0.0625rem 0.5625rem;
}
.afilter__text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.afilter__x {
  color: inherit;
  font-size: 0.875rem;
  height: 1.125rem;
  line-height: 1;
  min-height: 1.125rem;
  padding: 0;
  width: 1.125rem;
}
.afilter__x:hover { background: var(--color-primary); color: var(--color-primary-fg); }
.afilters__reset {
  color: var(--color-fg-faint);
  font-size: 0.6875rem;
  height: 1.5rem;
  min-height: 1.5rem;
  padding: 0 0.375rem;
}
</style>
