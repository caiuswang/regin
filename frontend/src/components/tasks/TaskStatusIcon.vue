<script setup>
// Status as a distinguishable SHAPE, not only a colour.
//
// A coloured dot is unreadable to a colour-blind reader and invisible in a
// greyscale screenshot, and status is the column a backlog is scanned by. Each
// state therefore gets its own geometry — hollow, ringed, quartered, half,
// check, cross, struck — and carries its name as the accessible label.
import { computed } from 'vue'

const props = defineProps({
  status: { type: String, default: 'idea' },
  size: { type: Number, default: 14 },
})

const status = computed(() => props.status || 'idea')
</script>

<template>
  <svg
    class="ts"
    :class="`ts-${status}`"
    :width="size"
    :height="size"
    viewBox="0 0 16 16"
    role="img"
    :aria-label="status"
    :data-status="status"
  >
    <title>{{ status }}</title>
    <circle class="ts-ring" cx="8" cy="8" r="6" />

    <circle v-if="status === 'armed'" class="ts-fill" cx="8" cy="8" r="2.5" />

    <path
      v-else-if="status === 'queued'"
      class="ts-fill"
      d="M8 2.5a5.5 5.5 0 0 1 0 11z"
    />

    <path
      v-else-if="status === 'running'"
      class="ts-fill"
      d="M8 2.5a5.5 5.5 0 0 1 5.5 5.5H8z"
    />

    <path
      v-else-if="status === 'done'"
      class="ts-mark"
      d="m5 8.2 2.2 2.2L11 6.2"
    />

    <g v-else-if="status === 'failed'" class="ts-mark">
      <path d="m5.6 5.6 4.8 4.8" />
      <path d="m10.4 5.6-4.8 4.8" />
    </g>

    <path v-else-if="status === 'archived'" class="ts-mark" d="M4 12 12 4" />
  </svg>
</template>

<style scoped>
.ts { display: block; flex-shrink: 0; }
.ts-ring {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.6;
}
.ts-fill { fill: currentColor; }
.ts-mark {
    fill: none;
    stroke: currentColor;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
}

.ts-idea { color: var(--color-fg-faint); }
.ts-armed { color: var(--color-info); }
.ts-queued { color: var(--color-warning); }
.ts-running { color: var(--color-agent); }
.ts-done { color: var(--color-success); }
.ts-failed { color: var(--color-danger); }
.ts-archived { color: var(--color-border-strong); }
</style>
