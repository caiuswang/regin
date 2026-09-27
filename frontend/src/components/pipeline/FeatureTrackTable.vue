<script setup>
// The Features table (1c): a stage track per feature, task progress, round
// pips and defects; a row expands to its blamed defects and fix tasks.
import { ref } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import StageTrack from './StageTrack.vue'
import FeatureExpansion from './FeatureExpansion.vue'
import { ago } from '../../utils/pipelineFormat'
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

// A pip per round; the rounds after the ship that escaped are the late ones.
function pips(row) {
  const n = Math.max(0, row.rounds || 0)
  const late = row.stage === 'escaped' ? Math.min(n, Math.max(1, row.defects?.escapes || 0)) : 0
  return Array.from({ length: Math.min(n, 8) }, (_, i) => i >= Math.min(n, 8) - late)
}

function cost(row) {
  return row.cost_usd == null ? 'none declared' : `≤ ${money(row.cost_usd)}`
}
</script>

<template>
  <div class="ftt" data-testid="feature-table" role="table" aria-label="Features">
    <div class="ftt-head" role="row">
      <span role="columnheader">Feature</span>
      <span role="columnheader">Stage</span>
      <span role="columnheader">Tasks</span>
      <span role="columnheader">Rounds</span>
      <span role="columnheader" class="num">Defects</span>
      <span role="columnheader" class="num">Active</span>
    </div>
    <div class="ftt-body">
      <div
        v-for="row in rows"
        :key="row.id"
        class="ftt-group"
        :class="{ 'is-expanded': expanded.has(row.id) }"
        role="rowgroup"
      >
        <div class="ftt-row" role="row" :data-testid="`feature-row-${row.id}`">
          <span class="ftt-feature" role="cell">
            <Button
              variant="ghost"
              size="icon"
              class="ftt-toggle"
              :aria-expanded="expanded.has(row.id)"
              :aria-label="`${expanded.has(row.id) ? 'Collapse' : 'Expand'} feature #${row.id}`"
              :data-testid="`feature-toggle-${row.id}`"
              @click="toggle(row.id)"
            >
              <Icon :name="expanded.has(row.id) ? 'chevron-down' : 'chevron-right'" :size="13" />
            </Button>
            <span class="ftt-id" data-testid="feature-id">#{{ row.id }}</span>
            <span class="ftt-name">
              <router-link :to="`/features/${row.id}`" class="ftt-title focus-visible:outline-2 focus-visible:outline-blue-500">{{ row.title }}</router-link>
              <span class="ftt-meta">{{ row.external_ref || 'no ref' }} · {{ cost(row) }}</span>
            </span>
          </span>
          <span role="cell"><StageTrack :stage="row.stage" /></span>
          <span role="cell" class="ftt-tasks">
            <router-link
              v-if="row.tasks"
              :to="tasksLink(row.id)"
              class="ftt-link focus-visible:outline-2 focus-visible:outline-blue-500"
              :data-testid="`feature-tasks-link-${row.id}`"
            >{{ row.tasks_done }} / {{ row.tasks }}</router-link>
            <span v-else>0 / 0</span>
            <span class="ftt-track"><span class="ftt-fill tone-success" :style="{ width: `${row.tasks ? (100 * row.tasks_done) / row.tasks : 0}%` }" /></span>
          </span>
          <span role="cell" class="ftt-pips" data-testid="feature-rounds" :aria-label="`${row.rounds} rounds`">
            <span v-for="(late, i) in pips(row)" :key="i" class="ftt-pip" :class="late ? 'tone-warning' : 'ftt-pip--done'" />
          </span>
          <span role="cell" class="num" :class="{ 'is-warn': row.defects.open }">
            <router-link
              v-if="row.defects.total"
              :to="defectsLink(row.id)"
              class="ftt-link focus-visible:outline-2 focus-visible:outline-blue-500"
              :data-testid="`feature-defects-link-${row.id}`"
            >{{ row.defects.open }} / {{ row.defects.total }}</router-link>
            <span v-else>0 / 0</span>
          </span>
          <span role="cell" class="num muted">{{ ago(row.last_activity) }}</span>
        </div>
        <div v-if="expanded.has(row.id)" class="ftt-expansion">
          <FeatureExpansion :feature="row" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ftt { min-width: 0; }
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
.ftt-pip { width: 8px; height: 8px; border-radius: 999px; }
.ftt-pip--done { background: var(--color-fg-subtle); }
.num { text-align: right; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.is-warn { color: var(--color-warning-strong); font-weight: 600; }
.muted { color: var(--color-fg-muted); }
.ftt-expansion { padding: 0 1rem 0.75rem 3rem; }

@media (max-width: 1279.98px) {
    .ftt-head, .ftt-row { grid-template-columns: minmax(10rem, 2fr) minmax(7rem, 1.2fr) minmax(4rem, 0.8fr) 4rem 3rem; }
    .ftt-head > :nth-child(4), .ftt-row > :nth-child(4) { display: none; }
}
</style>
