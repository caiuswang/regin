<script setup>
// Linear's project bar, read off feature stages: each segment is a share of
// the live (not abandoned) features, so shipped + escaped is the done-claim
// and the remainder is what is still spec or being built. Each segment is
// its own element rather than a gradient so a hover can name its count.
import { computed } from 'vue'
import { BAR_STAGES, percent } from '../../utils/projectFormat'

const props = defineProps({
  progress: { type: Object, required: true },
  byStage: { type: Object, default: () => ({}) },
  compact: { type: Boolean, default: false },
})

const pct = computed(() => percent(props.progress))
const segments = computed(() => {
  const live = props.progress.live || 0
  if (!live) return []
  return BAR_STAGES
    .map(stage => ({ stage, n: props.byStage[stage] || 0 }))
    .filter(s => s.n > 0)
    .map(s => ({ ...s, width: `${(100 * s.n) / live}%` }))
})
const label = computed(() => (pct.value == null
  ? 'No live features'
  : `${props.progress.done} of ${props.progress.live} features shipped`))
</script>

<template>
  <div class="pp" :class="{ 'pp--compact': compact }" data-testid="project-progress">
    <div
      class="pp__bar"
      role="progressbar"
      :aria-valuenow="pct ?? 0"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-label="label"
    >
      <span
        v-for="s in segments"
        :key="s.stage"
        class="pp__seg"
        :class="`pp__seg--${s.stage}`"
        :style="{ width: s.width }"
        :title="`${s.n} ${s.stage}`"
      ></span>
    </div>
    <span class="pp__label" data-testid="project-progress-label">
      <template v-if="pct != null"><strong>{{ pct }}%</strong> · {{ label }}</template>
      <template v-else>{{ label }}</template>
    </span>
  </div>
</template>

<style scoped>
.pp { display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; }
.pp--compact { flex-direction: row; align-items: center; gap: 0.625rem; }
.pp__bar {
    display: flex;
    height: 0.5rem;
    border-radius: 999px;
    overflow: hidden;
    background: var(--color-surface-2);
    flex: 1 1 auto;
    min-width: 4rem;
}
.pp--compact .pp__bar { height: 0.375rem; flex: 0 0 6rem; }
.pp__seg { display: block; height: 100%; }
.pp__seg--shipped { background: var(--color-emerald-500); }
.pp__seg--escaped { background: var(--color-amber-500); }
.pp__seg--building { background: var(--color-blue-500); }
.pp__seg--spec { background: var(--color-slate-300); }
.pp__label {
    font-size: 0.8125rem;
    color: var(--color-text-muted, #6b7280);
    white-space: nowrap;
}
.pp__label strong { color: var(--color-text, inherit); font-weight: 600; }
</style>
