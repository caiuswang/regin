<script setup>
// The loop as a level (Overview): a scene strip where each drawn actor
// stands for real rows, then the three stages as cards with a cell meter and
// fact keys. Same props as the classic loop; every number is a loop part.
import { computed } from 'vue'
import PixelSprite from '../PixelSprite.vue'
import { meterCells } from '../meter.js'
import { loopCount, loopFacts, loopNote, loopParts } from '../../../../utils/pipelineFormat'

const props = defineProps({
  loop: { type: Object, required: true },
  ready: { type: Number, default: 0 },
  compact: { type: Boolean, default: false },
  scope: { type: Object, default: () => ({}) },
})

// A crowd past four reads as noise, so a group draws at most four actors and
// its label carries the real count.
const MAX_ACTORS = 4

const ZONES = [
  { key: 'board', name: 'Task board', station: 'board', groups: [
    { key: 'running', stage: 'Tasks', words: 'running', sprite: 'bot.running', tone: 'run' },
    { key: 'failed', stage: 'Tasks', words: 'failed', sprite: 'bot.failed', tone: 'fail' },
  ] },
  { key: 'workshop', name: 'Workshop', station: 'anvil', groups: [
    { key: 'escaped', stage: 'Features', words: 'escaped', sprite: 'flag.escaped', tone: 'warn' },
  ] },
  { key: 'den', name: 'Bug den', station: 'den', groups: [
    { key: 'triage', stage: 'Defects', words: 'to triage', sprite: 'bug', tone: 'warn' },
    { key: 'open', stage: 'Defects', words: 'open', sprite: 'bug.cold', tone: 'muted' },
  ] },
]

const zones = computed(() => ZONES.map(zone => ({
  ...zone,
  groups: zone.groups.map((g) => {
    const n = loopCount(props.loop, g.stage, g.key)
    return { ...g, n, actors: Math.min(n, MAX_ACTORS) }
  }),
})))

const sceneLabel = computed(() => zones.value
  .map(z => `${z.name}: ${z.groups.map(g => `${g.n} ${g.words}`).join(', ')}.`)
  .join(' '))

const stages = computed(() => (props.loop.stages || []).map((stage) => {
  const parts = loopParts(stage.parts)
  return {
    ...stage,
    cells: meterCells(parts),
    barLabel: parts.map(p => `${p.label || p.key} ${p.n}`).join(', '),
    facts: loopFacts(stage, props.ready, props.scope),
  }
}))

const note = computed(() => loopNote(props.loop.fix_tasks))
</script>

<template>
  <section
    class="pl-panel px-loop"
    :class="{ 'px-loop--compact': compact }"
    data-pl-part="panel"
    data-testid="overview-loop"
    aria-label="The loop"
  >
    <header class="pl-panel__head" data-pl-part="panel-head">
      <h2 class="pl-panel__title" data-pl-part="panel-title">The loop</h2>
      <span class="pl-panel__sub px-loop__path" data-pl-part="panel-meta">Tasks → Features → Defects</span>
    </header>

    <div class="px-scene-box" data-testid="loop-scene-box">
      <div class="px-scene" role="img" :aria-label="sceneLabel" data-testid="loop-scene">
        <div v-for="zone in zones" :key="zone.key" class="px-zone" :data-testid="`loop-zone-${zone.key}`">
          <div class="px-zone__floor">
            <PixelSprite :name="zone.station" :scale="2" data-station />
            <template v-for="g in zone.groups" :key="g.key">
              <div v-if="g.n" class="px-group" :data-testid="`loop-group-${g.key}`">
                <span class="px-group__label" :class="`is-${g.tone}`" data-testid="loop-group-label">{{ g.n }} {{ g.words }}</span>
                <span class="px-group__actors">
                  <PixelSprite v-for="i in g.actors" :key="i" :name="g.sprite" :scale="2" data-actor />
                </span>
              </div>
            </template>
          </div>
          <span class="px-zone__name">{{ zone.name }}</span>
        </div>
      </div>
    </div>

    <div class="px-stages">
      <article
        v-for="stage in stages"
        :key="stage.label"
        class="px-stage"
        :data-testid="`loop-${stage.label.toLowerCase()}`"
      >
        <header class="px-stage__head">
          <h2 class="px-stage__label">{{ stage.label }}</h2>
          <span class="px-stage__total" data-testid="loop-total">{{ stage.total }}</span>
        </header>
        <div class="px-meter" role="img" :aria-label="stage.barLabel" data-testid="loop-bar">
          <span
            v-for="(tone, c) in stage.cells"
            :key="c"
            class="px-meter__cell"
            data-pl-part="meter-cell"
            :data-tone="tone"
          />
        </div>
        <p class="px-facts" data-testid="loop-facts">
          <router-link
            v-for="fact in stage.facts"
            :key="fact.key"
            :to="fact.to"
            class="px-fact"
            data-pl-part="key"
            :data-tone="fact.tone"
            :data-testid="`loop-fact-${fact.key}`"
          ><b>{{ fact.n }}</b> {{ fact.words }}</router-link>
        </p>
      </article>
    </div>

    <p v-if="note" class="px-loop__note" data-pl-part="panel-foot" data-testid="loop-note">
      <span class="px-loop__back">↺ Loop back</span> {{ note }}
    </p>
  </section>
</template>

<style scoped>
.px-loop__path { margin-left: auto; }

/* The strip scrolls inside its own box below its natural width (R9), so the
   page never does. */
.px-scene-box { overflow-x: auto; border-bottom: 1px solid var(--pl-frame); }
.px-scene { display: grid; grid-template-columns: repeat(3, minmax(13rem, 1fr)); min-width: 39rem; }
.px-zone { display: flex; flex-direction: column; min-width: 0; padding: 1.25rem 0.9rem 0.4rem; }
.px-zone + .px-zone { border-left: 1px dashed var(--pl-rule); }
.px-zone__floor {
    display: flex; align-items: flex-end; gap: 0.9rem; min-height: 3.25rem;
    padding-bottom: 2px; border-bottom: 2px solid var(--pl-frame);
}
.px-group { display: flex; flex-direction: column; gap: 0.3rem; }
.px-group__actors { display: flex; align-items: flex-end; gap: 0.3rem; }
.px-group__label, .px-zone__name {
    font-family: var(--pl-font); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; white-space: nowrap;
}
.px-group__label.is-run { color: var(--pl-run); }
.px-group__label.is-fail { color: var(--pl-fail); }
.px-group__label.is-warn { color: var(--pl-warn); }
.px-group__label.is-muted { color: var(--pl-muted); }
.px-zone__name { padding-top: 0.4rem; color: var(--pl-muted); }

.px-stages { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.px-stage { display: flex; flex-direction: column; gap: 0.75rem; min-width: 0; padding: 1rem 1.1rem; }
.px-stage + .px-stage { border-left: 1px dashed var(--pl-rule); }
.px-stage__head { display: flex; align-items: center; gap: 0.75rem; }
.px-stage__label {
    font-family: var(--pl-font); font-size: 12px; font-weight: 400; letter-spacing: 0.1em; text-transform: uppercase;
}
.px-stage__label { color: var(--pl-text); }
.px-stage__total {
    margin-left: auto; font-family: var(--pl-font); font-size: 32px; font-weight: 700; line-height: 1;
    color: var(--pl-text); font-variant-numeric: tabular-nums;
}

.px-meter { display: grid; grid-template-columns: repeat(24, minmax(0, 1fr)); gap: 2px; }
.px-meter__cell { height: 10px; }

.px-facts { display: flex; flex-wrap: wrap; gap: 0.5rem; min-height: 1.75rem; }
.px-fact {
    display: inline-flex; align-items: center; gap: 0.4em; height: 1.75rem; padding: 0 0.6rem;
    border: 1px solid; font-size: 10px; text-decoration: none; white-space: nowrap;
}
.px-fact b { font-weight: 700; }

.px-loop__note { display: flex; align-items: center; gap: 0.75rem; padding: 0.6rem 1.1rem; }
.px-loop__back { flex: none; color: var(--pl-action-text); }

.px-loop--compact .px-stages { grid-template-columns: minmax(0, 1fr); }
.px-loop--compact .px-stage { padding: 0.75rem 0.875rem; }
.px-loop--compact .px-stage + .px-stage { border-left: 0; border-top: 1px dashed var(--pl-rule); }
.px-loop--compact .px-fact { min-height: 44px; }
</style>
