<script setup>
// One feature as a phone card (2c). An escaped card is bordered and lists the
// defects blamed on it.
import PlSlot from './PlSlot.vue'
import FeatureExpansion from './FeatureExpansion.vue'
import FeatureNextStep from '../features/FeatureNextStep.vue'
import { ago } from '../../utils/pipelineFormat'

defineProps({
  feature: { type: Object, required: true },
})
</script>

<template>
  <article class="fc" :class="{ 'is-escaped': feature.stage === 'escaped' }" :data-testid="`feature-card-${feature.id}`">
    <header class="fc-head">
      <span class="fc-id" data-testid="feature-id">#{{ feature.id }}</span>
      <router-link :to="`/features/${feature.id}`" class="fc-title focus-visible:outline-2 focus-visible:outline-blue-500">{{ feature.title }}</router-link>
      <span class="fc-ago">{{ ago(feature.last_activity) }}</span>
    </header>
    <div class="fc-line">
      <PlSlot name="StageTrack" :stage="feature.stage" compact />
      <span class="fc-fig">tasks {{ feature.tasks_done }} / {{ feature.tasks }}</span>
      <span class="fc-fig" :class="{ 'is-warn': feature.defects.open }">defects {{ feature.defects.open }} / {{ feature.defects.total }}</span>
    </div>
    <FeatureNextStep :step="feature.next_step" />
    <FeatureExpansion v-if="feature.stage === 'escaped' && feature.defects.open" :feature="feature" compact />
  </article>
</template>

<style scoped>
.fc {
    display: flex; flex-direction: column; gap: 0.5rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface); padding: 0.8rem 0.9rem;
}
.fc.is-escaped { border-color: var(--color-amber-400); }
.fc-head { display: flex; align-items: baseline; gap: 0.45rem; min-width: 0; }
.fc-id { font-size: 12px; color: var(--color-fg-faint); flex: none; }
.fc-title {
    flex: 1; min-width: 0; font-size: 14.5px; font-weight: 700; color: var(--color-fg);
    text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    min-height: 24px;
}
.fc-ago { font-size: 12px; color: var(--color-fg-faint); flex: none; }
.fc-line { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.fc-fig { font-size: 12.5px; color: var(--color-fg-muted); font-variant-numeric: tabular-nums; }
.fc-line .fc-fig:first-of-type { margin-left: auto; }
.fc-fig.is-warn { color: var(--color-warning-strong); font-weight: 600; }
</style>
