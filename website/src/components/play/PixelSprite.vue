<script setup>
import { computed } from 'vue'

// Box-shadow pixel art: one 1-cell div whose shadows paint every lit cell.
const props = defineProps({
  rows: { type: Array, required: true },
  palette: { type: Object, required: true },
  px: { type: Number, default: 4 },
})

const shadow = computed(() => props.rows.flatMap((row, y) => [...row].flatMap((c, x) => (
  props.palette[c] ? [`${x * props.px}px ${y * props.px}px 0 var(--${props.palette[c]})`] : []
))).join(','))
</script>

<template>
  <div class="pixel-sprite" :style="{ width: `${px}px`, height: `${px}px`, boxShadow: shadow }"></div>
</template>
