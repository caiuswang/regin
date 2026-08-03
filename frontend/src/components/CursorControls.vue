<script setup>
// Load-more + row-count footer for cursor-paginated tables.
// Keeps the "how many am I seeing" affordance that a raw scroll list loses.
import { computed } from 'vue'
import Button from './ui/Button.vue'

const props = defineProps({
  count: { type: Number, required: true },
  hasNext: { type: Boolean, required: true },
  loadingMore: { type: Boolean, default: false },
  label: { type: String, default: 'events' },
  // Server-side total for the current filter set, when the endpoint reports
  // one. Turns "Showing 12 sessions…" into "Showing 12 of 13 sessions".
  total: { type: Number, default: null },
  // Optional trailing note, e.g. the pagination strategy.
  note: { type: String, default: '' },
})
defineEmits(['load-more'])

const shown = computed(() => (props.total != null
  ? `Showing ${props.count} of ${props.total} ${props.label}`
  : `Showing ${props.count} ${props.label}`))
</script>

<template>
  <div class="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-t border-gray-200 bg-gray-50 text-xs text-gray-500">
    <span>
      {{ shown }}<span v-if="hasNext && total == null">&hellip;</span>
      <span v-if="note" class="text-gray-400"> · {{ note }}</span>
    </span>
    <Button
      v-if="hasNext"
      variant="secondary"
      size="sm"
      :disabled="loadingMore"
      @click="$emit('load-more')"
    >{{ loadingMore ? 'Loading…' : 'Load more' }}</Button>
    <span v-else class="italic">End of results</span>
  </div>
</template>
