<script setup>
import { computed } from 'vue'

const props = defineProps({
  // Zero-filled per-bucket fire counts, oldest → newest. Length is
  // determined by the API based on the active Range filter.
  buckets: { type: Array, required: true },
  // 'noisy' | 'active' | 'dead' — drives bar color.
  status: { type: String, default: 'active' },
  width: { type: Number, default: 160 },
  height: { type: Number, default: 28 },
})

const max = computed(() => Math.max(1, ...props.buckets))
const barColor = computed(() => ({
  noisy:  '#fbbf24',  // amber
  active: '#60a5fa',  // blue
  dead:   '#cbd5e1',  // slate-300, near-invisible against bg
}[props.status] || '#60a5fa'))

const barW = computed(() => {
  const gap = props.buckets.length > 12 ? 1 : 2
  return Math.max(1, (props.width - gap * (props.buckets.length - 1)) / props.buckets.length)
})
const gap = computed(() => (props.buckets.length > 12 ? 1 : 2))

function barHeight(v) {
  // Reserve 1px floor so empty buckets still hint at the timeline.
  return Math.max(1, (v / max.value) * (props.height - 2))
}
function barY(v) { return props.height - barHeight(v) }
</script>

<template>
  <svg
    class="rule-spark"
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    preserveAspectRatio="none"
    role="img"
    :aria-label="`spark: ${buckets.join(', ')}`"
  >
    <rect
      v-for="(v, i) in buckets"
      :key="i"
      :x="i * (barW + gap)"
      :y="barY(v)"
      :width="barW"
      :height="barHeight(v)"
      :fill="barColor"
    />
  </svg>
</template>

<style scoped>
.rule-spark { display: block; }
</style>
