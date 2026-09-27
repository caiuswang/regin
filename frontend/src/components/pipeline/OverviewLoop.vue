<script setup>
// Tasks → Features → Defects, the loop the three tabs read (designs 1a, 1d,
// 2a). Every number is the server's; a stage's parts sum to its total.
import { computed } from 'vue'
import Icon from '../ui/Icon.vue'
import SegmentBar from './SegmentBar.vue'
import { PART_TONE } from '../../utils/pipelineFormat'

const props = defineProps({
  loop: { type: Object, required: true },
  compact: { type: Boolean, default: false },
  scoped: { type: Boolean, default: false },
})

const stages = computed(() => (props.loop.stages || []).map(stage => ({
  ...stage,
  parts: stage.parts.map(p => ({ ...p, tone: PART_TONE[p.key] || 'idle' })),
  hint: stage.hint || (props.scoped ? scopedHint(stage) : ''),
})))

// 1d's per-stage hint; the Tasks one is the server's own sentence.
function scopedHint(stage) {
  const part = key => stage.parts.find(p => p.key === key)?.n || 0
  if (stage.label === 'Features' && part('escaped')) return `${part('escaped')} escaped`
  if (stage.label === 'Defects' && part('triage')) return `${part('triage')} to triage`
  return ''
}

const note = computed(() => {
  const n = props.loop.fix_tasks || 0
  if (!n) return ''
  return `${n} fix task${n === 1 ? '' : 's'} carr${n === 1 ? 'ies' : 'y'} open defects back into the queue`
})
</script>

<template>
  <section class="ol" :class="{ 'ol--compact': compact }" data-testid="overview-loop" aria-label="The loop">
    <div class="ol-stages">
      <template v-for="(stage, i) in stages" :key="stage.label">
        <article class="ol-stage" :data-testid="`loop-${stage.label.toLowerCase()}`">
          <header class="ol-stage__head">
            <h2 class="ol-stage__label">{{ stage.label }}</h2>
            <span class="ol-spacer" />
            <span v-if="stage.hint" class="ol-stage__hint">{{ stage.hint }}</span>
            <span class="ol-stage__total" data-testid="loop-total">{{ stage.total }}</span>
          </header>
          <SegmentBar :parts="stage.parts" :height="compact ? 6 : 8" />
          <dl v-if="!compact && !scoped" class="ol-legend">
            <div
              v-for="part in stage.parts"
              :key="part.key"
              class="ol-part"
              :class="{ [`hl-${part.tone}`]: part.highlighted && part.n }"
              :data-testid="`loop-part-${part.key}`"
            >
              <dt><span class="ol-dot" :class="`tone-${part.tone}`" />{{ part.label }}</dt>
              <dd>{{ part.n }}</dd>
            </div>
          </dl>
        </article>
        <Icon v-if="!compact && i < stages.length - 1" name="arrow-right" :size="14" class="ol-arrow" />
      </template>
    </div>
    <p v-if="note" class="ol-note" data-testid="loop-note">
      <Icon name="rotate-ccw" :size="12" /> {{ note }}
    </p>
  </section>
</template>

<style scoped>
.ol { display: flex; flex-direction: column; gap: 0.5rem; }
.ol-stages { display: flex; align-items: stretch; gap: 0.6rem; }
.ol-stage {
    flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; gap: 0.7rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface); padding: 1rem 1.1rem;
}
.ol-stage__head { display: flex; align-items: baseline; gap: 0.6rem; }
.ol-stage__label {
    font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--color-fg-muted);
}
.ol-spacer { flex: 1; }
.ol-stage__hint { font-size: 12px; color: var(--color-fg-muted); }
.ol-stage__total { font-size: 22px; font-weight: 700; color: var(--color-fg); font-variant-numeric: tabular-nums; }
.ol-legend { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.25rem 0.9rem; }
.ol-part {
    display: flex; align-items: center; justify-content: space-between; gap: 0.5rem;
    padding: 0.15rem 0.35rem; border-radius: var(--radius-md); font-size: 12.5px;
}
.ol-part dt { display: flex; align-items: center; gap: 0.4rem; color: var(--color-fg-muted); min-width: 0; }
.ol-part dd { font-weight: 600; font-variant-numeric: tabular-nums; color: var(--color-fg); }
.ol-part.hl-danger { background: var(--color-danger-soft); }
.ol-part.hl-warning { background: var(--color-amber-200); }
.ol-part.hl-orange { background: var(--color-warning-soft); }
.ol-dot { width: 8px; height: 8px; border-radius: 999px; flex: none; }
.ol-arrow { align-self: center; flex: none; color: var(--color-fg-faint); }
.ol-note {
    display: flex; align-items: center; justify-content: center; gap: 0.35rem;
    font-size: 12.5px; color: var(--color-danger);
}

.ol--compact .ol-stages {
    flex-direction: column; gap: 0;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl) var(--radius-xl) 0 0;
    background: var(--color-surface); overflow: hidden;
}
.ol--compact .ol-stage { border: 0; border-radius: 0; padding: 0.8rem 1rem; gap: 0.5rem; }
.ol--compact .ol-stage + .ol-stage { border-top: 1px solid var(--color-border); }
.ol--compact .ol-note {
    justify-content: flex-start; margin-top: -0.5rem; padding: 0.55rem 1rem;
    border: 1px solid var(--color-border); border-top: 0;
    border-radius: 0 0 var(--radius-xl) var(--radius-xl);
    background: var(--color-danger-soft);
}
.ol--compact:not(:has(.ol-note)) .ol-stages { border-radius: var(--radius-xl); }

@media (max-width: 1023.98px) {
    .ol:not(.ol--compact) .ol-stages { flex-wrap: wrap; }
    .ol:not(.ol--compact) .ol-stage { flex-basis: 16rem; }
    .ol:not(.ol--compact) .ol-arrow { display: none; }
}
</style>
