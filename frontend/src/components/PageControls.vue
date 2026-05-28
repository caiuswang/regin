<script setup>
// Prev/next/goto pager for offset-limit tables. The page number is 0-based
// in the API but rendered as 1-based here.
import { computed } from 'vue'

const props = defineProps({
  page: { type: Number, required: true },
  pageCount: { type: Number, required: true },
  total: { type: Number, required: true },
  size: { type: Number, required: true },
  hasNext: { type: Boolean, required: true },
  hasPrev: { type: Boolean, required: true },
  loading: { type: Boolean, default: false },
  sizes: { type: Array, default: () => [25, 50, 100, 200] },
})
const emit = defineEmits(['prev', 'next', 'goto', 'set-size'])

const displayPage = computed(() => props.page + 1)
const rangeStart = computed(() => (props.total === 0 ? 0 : props.page * props.size + 1))
const rangeEnd = computed(() => Math.min(props.total, (props.page + 1) * props.size))

function onInput(evt) {
  const n = parseInt(evt.target.value, 10)
  if (!Number.isNaN(n)) emit('goto', n - 1)
}
</script>

<template>
  <div class="flex items-center justify-between px-4 py-3 border-t border-gray-200 bg-gray-50 text-xs text-gray-500 gap-3 flex-wrap">
    <span>{{ rangeStart }}-{{ rangeEnd }} of {{ total }}</span>
    <div class="flex items-center gap-2 flex-wrap">
      <button type="button" class="btn btn-secondary text-xs" :disabled="!hasPrev || loading" @click="emit('prev')">&larr; Prev</button>
      <span>Page
        <input
          type="number" min="1" :max="pageCount" :value="displayPage"
          aria-label="Page number"
          class="w-14 px-1 py-0.5 border border-gray-300 rounded text-center"
          @change="onInput"
        >
        of {{ pageCount }}
      </span>
      <button type="button" class="btn btn-secondary text-xs" :disabled="!hasNext || loading" @click="emit('next')">Next &rarr;</button>
      <span class="ml-2">Rows:
        <select
          class="px-1 py-0.5 border border-gray-300 rounded"
          aria-label="Rows per page"
          :value="size"
          @change="evt => emit('set-size', parseInt(evt.target.value, 10))"
        >
          <option v-for="s in sizes" :key="s" :value="s">{{ s }}</option>
        </select>
      </span>
    </div>
  </div>
</template>
