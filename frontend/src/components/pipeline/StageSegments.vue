<script setup>
// A feature's stage as a label, its step, and one bar segment per step
// reached. Escaped reads "Shipped" with a warning last segment: it did ship.
import { computed } from 'vue'

const props = defineProps({
  stage: { type: String, required: true },
})

const STEP = { spec: 1, building: 2, shipped: 3, escaped: 3 }
const LABEL = { spec: 'Spec', building: 'Building', shipped: 'Shipped', escaped: 'Shipped' }

const reached = computed(() => STEP[props.stage] || 0)
const shipped = computed(() => reached.value === 3)
const segs = computed(() => Array.from({ length: reached.value }, (_, i) => {
  if (!shipped.value) return 'muted'
  return props.stage === 'escaped' && i === 2 ? 'warning' : 'success'
}))
</script>

<template>
  <div class="ss" :data-stage="stage" data-testid="stage-segments">
    <div class="ss-head">
      <span class="ss-label" :class="{ 'is-shipped': shipped }">{{ LABEL[stage] || stage }}</span>
      <span class="ss-step" :class="{ 'is-escaped': stage === 'escaped' }" data-testid="stage-step">{{
        stage === 'escaped' ? 'escaped' : `${reached} of 3` }}</span>
    </div>
    <div class="ss-bar" aria-hidden="true">
      <i v-for="(tone, i) in segs" :key="i" class="ss-seg" :class="`ss-seg--${tone}`" />
    </div>
  </div>
</template>

<style scoped>
.ss { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.ss-head { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: 6px; white-space: nowrap; }
.ss-label { font-size: 12.5px; font-weight: 500; color: var(--color-fg); }
.ss-label.is-shipped { color: var(--color-success); }
.ss-step { font-size: 11px; color: var(--color-fg-faint); }
.ss-step.is-escaped { color: var(--color-warning-strong); font-weight: 600; }
.ss-bar { display: flex; gap: 3px; width: 120px; max-width: 100%; }
.ss-seg { flex: 1; height: 4px; border-radius: 2px; }
.ss-seg--muted { background: var(--color-fg-muted); }
.ss-seg--success { background: var(--color-success); }
.ss-seg--warning { background: var(--color-warning); }
</style>
