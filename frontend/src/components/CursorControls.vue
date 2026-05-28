<script setup>
// Load-more + row-count footer for cursor-paginated tables.
// Keeps the "how many am I seeing" affordance that a raw scroll list loses.
defineProps({
  count: { type: Number, required: true },
  hasNext: { type: Boolean, required: true },
  loadingMore: { type: Boolean, default: false },
  label: { type: String, default: 'events' },
})
defineEmits(['load-more'])
</script>

<template>
  <div class="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-t border-gray-200 bg-gray-50 text-xs text-gray-500">
    <span>Showing {{ count }} {{ label }}<span v-if="hasNext">&hellip;</span></span>
    <button
      v-if="hasNext"
      type="button"
      class="btn btn-secondary text-xs"
      :disabled="loadingMore"
      @click="$emit('load-more')"
    >{{ loadingMore ? 'Loading…' : 'Load more' }}</button>
    <span v-else class="italic">End of results</span>
  </div>
</template>
