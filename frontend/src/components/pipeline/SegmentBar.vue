<script setup>
// A stacked bar: each part's width is its share of the sum. A zero part draws
// nothing, and an all-zero bar draws the empty track rather than dividing by 0.
import { computed } from 'vue'

const props = defineProps({
  parts: { type: Array, required: true },
  height: { type: Number, default: 6 },
  label: { type: String, default: '' },
})

const total = computed(() => props.parts.reduce((n, p) => n + (p.n || 0), 0))
const drawn = computed(() => props.parts.filter(p => p.n > 0))
</script>

<template>
  <div
    class="sb"
    data-pl-part="meter-bar"
    :style="{ height: `${height}px` }"
    role="img"
    :aria-label="label || parts.map(p => `${p.label || p.key} ${p.n}`).join(', ')"
  >
    <span
      v-for="p in drawn"
      :key="p.key"
      class="sb-seg"
      :class="`tone-${p.tone}`"
      data-pl-part="meter-cell"
      :data-tone="p.tone"
      :style="{ flexGrow: p.n }"
      :data-part="p.key"
    />
    <span v-if="!total" class="sb-empty" />
  </div>
</template>

<style scoped>
.sb {
    display: flex; gap: 2px; width: 100%; min-width: 0;
    border-radius: 999px; overflow: hidden;
}
.sb-seg { flex-basis: 0; min-width: 3px; }
.sb-empty { flex: 1; background: var(--color-surface-3); }
</style>
