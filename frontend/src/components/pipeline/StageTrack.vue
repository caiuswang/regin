<script setup>
// Spec → building → shipped as three nodes on a line. Escaped is a shipped
// track whose last node turned warning and wears a ring.
import { computed } from 'vue'

const props = defineProps({
  stage: { type: String, required: true },
  compact: { type: Boolean, default: false },
  showLabel: { type: Boolean, default: true },
})

const reached = computed(() => ({
  spec: 1, building: 2, shipped: 3, escaped: 3,
}[props.stage] || 0))
const tone = computed(() => ({
  spec: 'info', building: 'info', shipped: 'success', escaped: 'success',
}[props.stage] || 'idle'))
</script>

<template>
  <div class="st" :class="{ 'st--compact': compact }" :data-stage="stage">
    <div class="st-track" aria-hidden="true">
      <template v-for="n in 3" :key="n">
        <span
          class="st-node"
          :class="[
            n <= reached ? `tone-${tone}` : 'st-node--off',
            { 'st-node--escaped': stage === 'escaped' && n === 3 },
          ]"
        />
        <span
          v-if="n < 3"
          class="st-line"
          :class="n < reached ? `tone-${stage === 'escaped' && n === 2 ? 'warning' : tone}` : 'st-line--off'"
        />
      </template>
    </div>
    <span v-if="showLabel" class="st-label" :class="`text-tone-${
      { spec: 'muted', building: 'info', shipped: 'success', escaped: 'warning' }[stage] || 'muted'}`"
    >{{ stage }}</span>
  </div>
</template>

<style scoped>
.st { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.st--compact { flex-direction: row; align-items: center; gap: 0.5rem; }
.st-track { display: flex; align-items: center; width: 8.5rem; max-width: 100%; }
.st--compact .st-track { width: 5rem; }
.st-node { width: 9px; height: 9px; border-radius: 999px; flex: none; }
.st-node--off { background: var(--color-border-strong); }
.st-node--escaped {
    background: var(--color-amber-400);
    width: 13px; height: 13px;
    box-shadow: 0 0 0 3px var(--color-warning-soft);
}
.st-line { flex: 1; height: 2px; }
.st-line--off { background: var(--color-border-strong); }
.st-label { font-size: 11.5px; font-weight: 600; }
</style>
