<script setup>
// Priority as a bar glyph rather than a word: it sits in a dense row where
// four characters of "high" would cost more width than the title can spare,
// and the filled-bar count is readable at a glance down a column.
import { computed } from 'vue'
import { priorityMeta } from '../../utils/taskMeta.js'

const props = defineProps({
  value: { type: String, default: 'none' },
  size: { type: Number, default: 14 },
})

const meta = computed(() => priorityMeta(props.value))
const bars = [1, 2, 3, 4]
</script>

<template>
  <svg
    class="tp"
    :class="`tp-${meta.tone}`"
    :width="size"
    :height="size"
    viewBox="0 0 16 16"
    role="img"
    :aria-label="meta.label"
  >
    <title>{{ meta.label }}</title>
    <rect
      v-for="n in bars"
      :key="n"
      class="tp-bar"
      :class="{ 'tp-on': n <= meta.bars }"
      :x="1 + (n - 1) * 4"
      :y="13 - n * 3"
      width="2.5"
      :height="n * 3"
      rx="1"
    />
  </svg>
</template>

<style scoped>
.tp { display: block; flex-shrink: 0; }
.tp-bar { fill: var(--color-border-strong); }
.tp-on { fill: currentColor; }
.tp-danger { color: var(--color-danger); }
.tp-warning { color: var(--color-warning); }
.tp-info { color: var(--color-info); }
.tp-muted { color: var(--color-fg-muted); }
.tp-faint { color: var(--color-fg-faint); }
</style>
