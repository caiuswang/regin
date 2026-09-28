<script setup>
// All · Spec · Building · Shipped · Escaped, each with the server's stage-blind
// count (`extras.stage_counts`), so All is always the sum of the four.
import { computed } from 'vue'
import Button from '../ui/Button.vue'

const props = defineProps({
  counts: { type: Object, default: null },
  stage: { type: String, default: '' },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['select'])

const DESKTOP = ['', 'spec', 'building', 'shipped', 'escaped']
// Mobile leads with what needs attention (design 2c).
const MOBILE = ['', 'escaped', 'building', 'shipped', 'spec']
const LABEL = { '': 'All', spec: 'Spec', building: 'Building', shipped: 'Shipped', escaped: 'Escaped' }

const tabs = computed(() => (props.compact ? MOBILE : DESKTOP).map(key => ({
  key, label: LABEL[key], n: props.counts ? props.counts[key || 'all'] : null,
})))
</script>

<template>
  <div class="fst" :class="{ 'fst--compact': compact }" role="tablist" aria-label="Feature stage" data-testid="feature-stage-tabs">
    <Button
      v-for="t in tabs"
      :key="t.key || 'all'"
      variant="ghost"
      size="sm"
      role="tab"
      class="fst-tab"
      :class="[`fst-tab--${t.key || 'all'}`, { 'is-active': stage === t.key }]"
      :aria-selected="stage === t.key"
      :data-testid="`feature-stage-${t.key || 'all'}`"
      @click="emit('select', t.key)"
    >
      {{ t.label }}
      <b v-if="t.n != null" class="fst-n" :data-testid="`feature-stage-count-${t.key || 'all'}`">{{ t.n }}</b>
    </Button>
  </div>
</template>

<style scoped>
.fst { display: flex; align-items: center; min-width: 0; }
.fst .fst-tab {
    height: 32px; gap: 0.35rem; padding: 0 1rem 0 1.1rem; border-radius: 0;
    clip-path: polygon(0 0, calc(100% - 9px) 0, 100% 50%, calc(100% - 9px) 100%, 0 100%, 9px 50%);
    margin-left: -5px; font-size: 13px; font-weight: 500;
    background: var(--color-surface-2); color: var(--color-fg-muted);
}
.fst .fst-tab:first-child { margin-left: 0; clip-path: polygon(0 0, calc(100% - 9px) 0, 100% 50%, calc(100% - 9px) 100%, 0 100%); border-radius: var(--radius-lg) 0 0 var(--radius-lg); }
.fst .fst-tab--shipped { color: var(--color-success); }
.fst .fst-tab--escaped { color: var(--color-warning-strong); font-weight: 600; }
.fst--compact .fst-tab--shipped { color: var(--color-fg-muted); }
.fst .fst-tab--spec .fst-n { color: var(--color-fg); }
.fst .fst-tab.is-active { background: var(--color-fg); color: var(--color-surface); }
.fst-n { font-weight: 700; font-variant-numeric: tabular-nums; }

.fst--compact { gap: 0.4rem; overflow-x: auto; scrollbar-width: none; padding-bottom: 2px; }
.fst--compact .fst-tab, .fst--compact .fst-tab:first-child {
    clip-path: none; margin: 0; border-radius: 999px; height: 44px; padding: 0 1rem; flex: none;
}
</style>
