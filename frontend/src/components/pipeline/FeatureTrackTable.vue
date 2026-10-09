<script setup>
// The Features table (1c): a stage bar per feature, task progress, round
// pips and defects; a row expands to its active tasks, blamed defects and fix tasks.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import StageSegments from './StageSegments.vue'
import PlSlot from './PlSlot.vue'
import FeatureExpansion from './FeatureExpansion.vue'
import FeatureNextStep from '../features/FeatureNextStep.vue'
import FeatureFollowsChips from '../features/FeatureFollowsChips.vue'
import { ago, defectTally } from '../../utils/pipelineFormat'
import { money } from '../../utils/ledgerFormat'
import { defectsLink, tasksLink } from '../../utils/featureLinks'

defineProps({
  rows: { type: Array, required: true },
})

const expanded = ref(new Set())
function toggle(id) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

function pips(row) {
  return Math.min(Math.max(0, row.rounds || 0), 8)
}

function cost(row) {
  return row.cost_usd == null ? 'none declared' : `≤ ${money(row.cost_usd)}`
}
</script>

<template>
  <div class="ftt" data-testid="feature-table" data-pl-part="table" role="table" aria-label="Features">
    <div class="ftt-head" data-pl-part="table-head" role="row">
      <span role="columnheader">Feature</span>
      <PlSlot name="FeatureTableHead">
        <span role="columnheader">Stage</span>
        <span role="columnheader">Tasks</span>
        <span role="columnheader">Rounds</span>
        <span role="columnheader" class="num">Defects</span>
        <span role="columnheader" class="num">Active</span>
      </PlSlot>
    </div>
    <div class="ftt-body" data-pl-part="rows">
      <div
        v-for="row in rows"
        :key="row.id"
        class="ftt-group"
        :class="{ 'is-expanded': expanded.has(row.id) }"
        data-pl-part="row"
        :data-expanded="expanded.has(row.id) ? '' : undefined"
        role="rowgroup"
      >
        <div class="ftt-row" role="row" data-pl-part="row-line" :data-testid="`feature-row-${row.id}`">
          <span class="ftt-feature" role="cell">
            <Button
              variant="ghost"
              size="icon"
              class="ftt-toggle"
              data-pl-part="row-toggle"
              :aria-expanded="expanded.has(row.id)"
              :aria-label="`${expanded.has(row.id) ? 'Collapse' : 'Expand'} feature #${row.id}`"
              :data-testid="`feature-toggle-${row.id}`"
              @click="toggle(row.id)"
            >
              <Icon :name="expanded.has(row.id) ? 'chevron-down' : 'chevron-right'" :size="13" />
            </Button>
            <span class="ftt-id" data-testid="feature-id" data-pl-part="row-id">#{{ row.id }}</span>
            <span class="ftt-name">
              <router-link :to="`/features/${row.id}`" class="ftt-title focus-visible:outline-2 focus-visible:outline-blue-500">{{ row.title }}</router-link>
              <span class="ftt-meta">{{ row.external_ref || 'no ref' }} · {{ cost(row) }}</span>
              <FeatureFollowsChips :ids="row.follows || []" />
              <FeatureNextStep :step="row.next_step" />
            </span>
          </span>
          <span role="cell">
            <PlSlot name="StageTrack" :stage="row.stage" :live="row.next_step?.kind === 'watch'" :open-defects="row.defects.open">
              <StageSegments :stage="row.stage" />
            </PlSlot>
          </span>
          <PlSlot name="FeatureStats" :row="row">
            <span role="cell" class="ftt-tasks" data-pl-part="stat">
              <router-link
                v-if="row.tasks"
                :to="tasksLink(row.id)"
                class="ftt-link focus-visible:outline-2 focus-visible:outline-blue-500"
                :data-testid="`feature-tasks-link-${row.id}`"
              >{{ row.tasks_done }} / {{ row.tasks }}</router-link>
              <span v-else>0 / 0</span>
              <span class="ftt-track" data-pl-part="meter"><span class="ftt-fill tone-success" data-pl-part="meter-fill" :style="{ width: `${row.tasks ? (100 * row.tasks_done) / row.tasks : 0}%` }" /></span>
            </span>
            <span role="cell" class="ftt-pips" data-testid="feature-rounds" data-pl-part="pips" :aria-label="`${row.rounds} rounds`">
              <span v-for="i in pips(row)" :key="i" class="ftt-pip" data-pl-part="pip" />
            </span>
            <span role="cell" class="num" :class="{ 'is-warn': row.defects.open }" data-pl-part="stat" :data-warn="row.defects.open ? '' : undefined">
              <router-link
                v-if="row.defects.total"
                :to="defectsLink(row.id)"
                class="ftt-link focus-visible:outline-2 focus-visible:outline-blue-500"
                :data-testid="`feature-defects-link-${row.id}`"
              >{{ defectTally(row.defects) }}</router-link>
              <span v-else>{{ defectTally(row.defects) }}</span>
            </span>
            <span role="cell" class="num muted" data-pl-part="stat">{{ ago(row.last_activity) }}</span>
          </PlSlot>
        </div>
        <div v-if="expanded.has(row.id)" class="ftt-expansion" data-pl-part="row-expansion">
          <FeatureExpansion :feature="row" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ftt { min-width: 0; container: ftt / inline-size; }
.ftt-head, .ftt-row {
    display: grid; align-items: center; gap: 1rem;
    grid-template-columns: minmax(12rem, 2.6fr) minmax(7.5rem, 1fr) minmax(4.5rem, 0.8fr) minmax(4rem, 0.7fr) 4rem 3rem;
    padding: 0.55rem 1rem;
}
.ftt-head {
    font-size: 10.5px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--color-fg-faint);
}
.ftt-body { border: 1px solid var(--color-border); border-radius: var(--radius-xl); background: var(--color-surface); overflow: hidden; }
.ftt-group + .ftt-group { border-top: 1px solid var(--color-border-subtle); }
.ftt-group.is-expanded { background: var(--color-surface-2); }
[data-theme="dark"] .ftt-body { background: var(--color-slate-50); border-color: transparent; }
[data-theme="dark"] .ftt-group.is-expanded { background: var(--color-slate-100); }
.ftt-row { padding-top: 0.7rem; padding-bottom: 0.7rem; }
.ftt-feature { display: flex; align-items: center; gap: 0.4rem; min-width: 0; }
.ftt-feature .ftt-toggle { width: 24px; height: 24px; flex: none; margin-left: -0.4rem; }
.ftt-id { font-size: 12px; color: var(--color-fg-faint); flex: none; }
.ftt-name { display: flex; flex-direction: column; min-width: 0; }
.ftt-title {
    font-size: 13.5px; font-weight: 600; color: var(--color-fg); text-decoration: none;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ftt-title:hover { text-decoration: underline; }
.ftt-meta { font-size: 11.5px; color: var(--color-fg-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ftt-tasks { display: flex; flex-direction: column; gap: 0.25rem; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.ftt-track { width: 100%; max-width: 6rem; height: 4px; border-radius: 999px; background: var(--color-surface-3); overflow: hidden; }
.ftt-fill { display: block; height: 100%; }
.ftt-link { color: inherit; text-decoration: none; }
.ftt-link:hover { text-decoration: underline; }
.ftt-pips { display: flex; gap: 4px; flex-wrap: wrap; }
.ftt-pip { width: 8px; height: 8px; border-radius: 999px; background: var(--color-border-strong); }
.num { text-align: right; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.is-warn { color: var(--color-warning-strong); font-weight: 600; }
.muted { color: var(--color-fg-muted); }
.ftt-expansion { padding: 0 1rem 0.75rem 3rem; }

/* Tiers key off the table's own width, not the viewport: beside the triage
   rail the table is ~300px even on a 1024px screen. */
@container ftt (max-width: 44rem) {
    .ftt-head, .ftt-row { grid-template-columns: minmax(10rem, 2fr) minmax(7rem, 1.2fr) minmax(4rem, 0.8fr) 4rem 3rem; }
    .ftt-head > :nth-child(4), .ftt-row > :nth-child(4) { display: none; }
}
@container ftt (max-width: 34rem) {
    .ftt-head, .ftt-row {
        grid-template-columns: minmax(0, 2fr) minmax(6rem, 1.2fr) minmax(3.5rem, 0.8fr) 3.5rem 3rem;
        gap: 0.75rem; padding-left: 0.75rem; padding-right: 0.75rem;
    }
    .ftt-expansion { padding-left: 2.25rem; padding-right: 0.75rem; }
}
@container ftt (max-width: 24rem) {
    .ftt-head, .ftt-row { grid-template-columns: minmax(0, 2fr) minmax(6rem, 1.2fr) 3rem 3rem; }
    .ftt-head > :nth-child(3), .ftt-row > :nth-child(3) { display: none; }
}
</style>
