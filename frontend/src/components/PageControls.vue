<script setup>
// Prev/next/goto pager for offset-limit tables. The page number is 0-based
// in the API but rendered as 1-based here.
import { computed } from 'vue'
import Button from './ui/Button.vue'
import Select from './ui/Select.vue'

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
  <div class="flex items-center justify-between px-4 py-2.5 border-t border-gray-200 bg-gray-50 text-xs text-gray-500 gap-3 flex-wrap">
    <span class="font-mono tabular-nums">{{ rangeStart }}–{{ rangeEnd }} <span class="text-gray-400">of</span> {{ total }}</span>
    <div class="flex items-center gap-1.5 flex-wrap">
      <Button variant="secondary" size="sm" :disabled="!hasPrev || loading" @click="emit('prev')">&larr; Prev</Button>
      <span class="inline-flex items-center gap-1.5 whitespace-nowrap px-1">
        Page
        <input
          type="number" min="1" :max="pageCount" :value="displayPage"
          aria-label="Page number"
          class="input w-12 px-1 py-0.5 text-center tabular-nums"
          @change="onInput"
        >
        <span class="text-gray-400">of {{ pageCount }}</span>
      </span>
      <Button variant="secondary" size="sm" :disabled="!hasNext || loading" @click="emit('next')">Next &rarr;</Button>
      <span class="ml-2 inline-flex items-center gap-1.5 whitespace-nowrap">Rows
        <span class="inline-block w-[4.75rem]">
          <Select
            block
            aria-label="Rows per page"
            :model-value="size"
            :options="sizes"
            @update:model-value="v => emit('set-size', parseInt(v, 10))"
          />
        </span>
      </span>
    </div>
  </div>
</template>
