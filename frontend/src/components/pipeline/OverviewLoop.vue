<script setup>
// Tasks → Features → Defects, the loop the three tabs read (designs 1a, 1d,
// 2a — the Quiet loop). Every part stays in the bar and its accessible name;
// the facts line prints only the counts that move or ask for a decision.
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import SegmentBar from './SegmentBar.vue'
import { loopParts } from '../../utils/pipelineFormat'

const props = defineProps({
  loop: { type: Object, required: true },
  ready: { type: Number, default: 0 },
  compact: { type: Boolean, default: false },
  featuresTo: { type: Function, required: true },
})

const FACTS = {
  Tasks: [['running', 'running'], ['ready', 'ready'], ['failed', 'failed', 'danger']],
  Features: [['building', 'building'], ['spec', 'in spec'], ['escaped', 'escaped', 'warn']],
  Defects: [['open', 'open'], ['triage', 'to triage', 'warn']],
}

function linkOf(key) {
  if (key === 'escaped') return props.featuresTo(null, 'escaped')
  if (key === 'triage') return props.featuresTo()
  return null
}

function factsOf(stage) {
  const n = key => (key === 'ready' ? props.ready : stage.parts.find(p => p.key === key)?.n || 0)
  return (FACTS[stage.label] || [])
    .map(([key, words, tone]) => ({ key, words, tone, n: n(key), to: linkOf(key) }))
    .filter(f => f.n > 0)
}

const stages = computed(() => (props.loop.stages || []).map(stage => ({
  ...stage, parts: loopParts(stage.parts), facts: factsOf(stage),
})))

const note = computed(() => {
  const n = props.loop.fix_tasks || 0
  if (!n) return ''
  return `${n} fix task${n === 1 ? '' : 's'} carr${n === 1 ? 'ies' : 'y'} open defects back into the queue`
})
</script>

<template>
  <section class="ol" :class="{ 'ol--compact': compact }" data-testid="overview-loop" aria-label="The loop">
    <div class="ol-stages">
      <article
        v-for="stage in stages"
        :key="stage.label"
        class="ol-stage"
        :data-testid="`loop-${stage.label.toLowerCase()}`"
      >
        <header class="ol-stage__head">
          <h2 class="ol-stage__label">{{ stage.label }}</h2>
          <span class="ol-stage__total" data-testid="loop-total">{{ stage.total }}</span>
        </header>
        <SegmentBar :parts="stage.parts" :height="6" data-testid="loop-bar" />
        <p class="ol-facts" data-testid="loop-facts">
          <template v-for="fact in stage.facts" :key="fact.key">
            <router-link
              v-if="fact.to"
              :to="fact.to"
              class="ol-fact ol-fact--link focus-visible:outline-2 focus-visible:outline-blue-500"
              :class="fact.tone && `is-${fact.tone}`"
              :data-testid="`loop-fact-${fact.key}`"
            >{{ fact.n }} {{ fact.words }}</router-link>
            <span
              v-else
              class="ol-fact"
              :class="fact.tone && `is-${fact.tone}`"
              :data-testid="`loop-fact-${fact.key}`"
            ><b>{{ fact.n }}</b> {{ fact.words }}</span>
          </template>
        </p>
      </article>
    </div>
    <p v-if="note" class="ol-note" data-testid="loop-note">
      <Icon name="rotate-ccw" :size="12" /> {{ note }}
    </p>
  </section>
</template>

<style scoped>
.ol {
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface); overflow: hidden;
}
.ol-stages { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.ol-stage { min-width: 0; display: flex; flex-direction: column; gap: 0.625rem; padding: 1rem 1.125rem; }
.ol-stage + .ol-stage { border-left: 1px solid var(--color-border); }
.ol-stage__head { display: flex; align-items: baseline; gap: 0.5rem; }
.ol-stage__label { font-size: 13px; font-weight: 600; color: var(--color-fg-muted); }
.ol-stage__total {
    margin-left: auto; font-size: 22px; font-weight: 700; letter-spacing: -0.01em;
    color: var(--color-fg); font-variant-numeric: tabular-nums;
}
.ol-facts {
    display: flex; flex-wrap: wrap; column-gap: 0.875rem; row-gap: 0.125rem; min-height: 1.1rem;
    font-size: 12.5px; color: var(--color-fg-muted); font-variant-numeric: tabular-nums;
}
.ol-fact b { font-weight: 600; color: var(--color-fg); }
.ol-fact.is-danger { font-weight: 600; color: var(--color-danger); }
.ol-fact.is-warn { font-weight: 600; color: var(--color-warning-strong); }
.ol-fact--link { text-decoration: none; }
.ol-fact--link:hover { text-decoration: underline; }
.ol-note {
    display: flex; align-items: center; gap: 0.35rem;
    padding: 0.5rem 1.125rem; border-top: 1px solid var(--color-border-subtle);
    font-size: 12px; color: var(--color-fg-subtle);
}

.ol--compact .ol-stages { grid-template-columns: minmax(0, 1fr); }
.ol--compact .ol-stage { padding: 0.75rem 0.875rem; gap: 0.5rem; }
.ol--compact .ol-stage + .ol-stage { border-left: 0; border-top: 1px solid var(--color-border-subtle); }
.ol--compact .ol-note { padding: 0.5rem 0.875rem; }
</style>
