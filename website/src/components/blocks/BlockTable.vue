<script setup>
import { computed } from 'vue'

const props = defineProps({
  cols: { type: Array, required: true },
  rows: { type: Array, required: true },
  mono: { type: Array, default: () => [0] },
  grid: { type: String, default: '' },
  minW: { type: String, default: '' },
})

// Column template and min width are per-table layout, not design tokens, so
// they ride in as custom properties the stylesheet reads.
const layout = computed(() => ({
  '--table-grid': props.grid || `repeat(${props.cols.length}, minmax(0, 1fr))`,
  '--table-min-w': props.minW || 'auto',
}))

function cellClass(ci) {
  if (!props.mono.includes(ci)) return ci === 0 ? 'cell-lead' : 'cell'
  return ci === props.mono[0] ? 'cell-mono-lead' : 'cell-mono'
}
</script>

<template>
  <div class="block-table" role="table" :style="layout">
    <div class="block-table-head" role="row">
      <span v-for="col in cols" :key="col" role="columnheader">{{ col }}</span>
    </div>
    <div v-for="(row, ri) in rows" :key="ri" class="block-table-row" role="row">
      <span v-for="(cell, ci) in row" :key="ci" role="cell" :class="cellClass(ci)">{{ cell }}</span>
    </div>
  </div>
</template>
