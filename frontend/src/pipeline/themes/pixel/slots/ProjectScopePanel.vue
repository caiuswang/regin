<script setup>
// One project picked (Overview, under a scope): the breadcrumb head, the
// project's stages as one cell per feature, its three figures as keys, and its
// most active features. Same props, emits and links as the classic panel.
import { computed } from 'vue'
import Button from '../../../../components/ui/Button.vue'
import { meterCells, METER_CELLS } from '../meter.js'
import { STAGES, ago } from '../../../../utils/pipelineFormat'
import { money } from '../../../../utils/ledgerFormat'

const props = defineProps({
  head: { type: Object, required: true },
  features: { type: Object, required: true },
  featuresTo: { type: Object, required: true },
})
const emit = defineEmits(['all'])

// Settled work first, so the row fills left to right like a progress bar.
const CELL_ORDER = ['shipped', 'escaped', 'building', 'spec']
// A task strip wider than this stops reading as a count and becomes a bar.
const TASK_CELLS = 8

const legend = computed(() => STAGES.map(stage => ({ stage, n: Number(props.head.stage_bar?.[stage] || 0) })))

// One cell per live feature while that stays countable; past a meter's width
// the cells become shares, as the loop's meters are.
const stageCells = computed(() => {
  const parts = CELL_ORDER.map(stage => ({ n: Number(props.head.stage_bar?.[stage] || 0), tone: stage }))
  const total = parts.reduce((n, p) => n + p.n, 0)
  return meterCells(parts, Math.min(total, METER_CELLS))
})

function taskCells(f) {
  if (!f.tasks) return []
  const width = Math.min(f.tasks, TASK_CELLS)
  return meterCells([{ n: f.tasks_done, tone: 'settled' }, { n: f.tasks - f.tasks_done, tone: 'unstarted' }], width)
}

const pct = computed(() => (props.head.shipped_pct == null ? null : `${props.head.shipped_pct}%`))
</script>

<template>
  <section class="pl-panel px-ps" data-pl-part="panel" data-testid="project-scope" aria-labelledby="px-ps-title">
    <div data-testid="project-head">
      <header class="pl-panel__head px-ps__head" data-pl-part="panel-head">
        <nav class="px-ps__trail" aria-label="Breadcrumb">
          <Button
            variant="link"
            size="sm"
            class="px-ps__all"
            data-pl-part="panel-link"
            data-testid="project-head-all"
            @click="emit('all')"
          >{{ head.breadcrumb[0] }}</Button>
          <span aria-hidden="true">/</span>
        </nav>
        <h2 id="px-ps-title" class="pl-panel__title px-ps__name" data-pl-part="panel-title">{{ head.breadcrumb[1] }}</h2>
        <span class="pl-panel__sub px-ps__count" data-pl-part="panel-meta" data-testid="project-head-count">{{ features.total }} features</span>
      </header>

      <div class="px-ps__band">
        <div class="px-ps__progress">
          <p class="px-ps__pct" data-testid="project-head-pct">
            <template v-if="pct"><b>{{ pct }}</b> shipped</template>
            <template v-else>nothing live</template>
          </p>
          <div
            class="px-ps__cells"
            role="img"
            :aria-label="legend.map(l => `${l.n} ${l.stage}`).join(', ')"
            data-testid="project-head-cells"
          >
            <span
              v-for="(tone, i) in stageCells"
              :key="i"
              class="px-cell px-cell--stage"
              data-pl-part="meter-cell"
              :data-tone="tone"
            />
          </div>
          <ul class="px-ps__legend" data-testid="project-head-legend">
            <li v-for="l in legend" :key="l.stage" :class="`is-${l.stage}`" :data-testid="`project-head-legend-${l.stage}`">
              <span class="px-cell px-cell--mark" data-pl-part="meter-cell" :data-tone="l.stage" />
              {{ l.n }} {{ l.stage }}
            </li>
          </ul>
        </div>

        <div class="px-ps__figs">
          <router-link
            class="px-fig"
            data-pl-part="key"
            :to="{ path: '/tasks', query: featuresTo.query }"
            data-testid="project-head-tasks"
          >
            <span class="px-fig__label">open tasks</span>
            <span class="px-fig__value">{{ head.open_tasks }}</span>
          </router-link>
          <router-link
            class="px-fig"
            data-pl-part="key"
            :data-tone="head.open_defects ? 'warn' : null"
            :to="{ path: '/defects', query: { ...featuresTo.query, status: 'open' } }"
            data-testid="project-head-defects"
          >
            <span class="px-fig__label">open defects</span>
            <span class="px-fig__value">{{ head.open_defects }}</span>
          </router-link>
          <div class="px-fig px-fig--still" data-testid="project-head-spent">
            <span class="px-fig__label">spent</span>
            <span class="px-fig__value">{{ head.spent_usd == null ? '—' : money(head.spent_usd) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="px-ps__table-box" data-testid="project-features">
      <table v-if="features.rows.length" class="px-ps__table">
        <thead>
          <tr>
            <th scope="col">Features in this project</th>
            <th scope="col">Stage</th>
            <th scope="col" class="px-col-tasks">Tasks</th>
            <th scope="col" class="num">Defects</th>
            <th scope="col" class="num px-col-active">Active</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in features.rows" :key="f.id" data-pl-part="row" :data-testid="`project-feature-${f.id}`">
            <td class="px-ps__title">
              <span class="px-ps__id" data-pl-part="row-id">#{{ f.id }}</span>
              <router-link :to="`/features/${f.id}`" class="focus-visible:outline-2 focus-visible:outline-blue-500">{{ f.title }}</router-link>
            </td>
            <td>
              <span class="px-ps__stage" :class="`is-${f.stage}`" data-testid="project-feature-stage">
                <span class="px-cell px-cell--mark" data-pl-part="meter-cell" :data-tone="f.stage" />
                {{ f.stage }}
              </span>
            </td>
            <td class="px-col-tasks">
              <span class="px-ps__tasks">
                <span class="px-ps__task-cells">
                  <span
                    v-for="(tone, i) in taskCells(f)"
                    :key="i"
                    class="px-cell px-cell--task"
                    data-pl-part="meter-cell"
                    :data-tone="tone"
                  />
                </span>
                {{ f.tasks_done }} / {{ f.tasks }}
              </span>
            </td>
            <td class="num" :class="{ 'is-warn': f.defects.open }">{{ f.defects.open }} / {{ f.defects.total }}</td>
            <td class="num px-col-active">{{ ago(f.last_activity) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="pl-panel__empty px-ps__empty" data-pl-part="panel-empty" data-testid="project-features-empty">
        No feature is filed under this project yet.
      </p>
    </div>

    <footer class="pl-panel__foot" data-pl-part="panel-foot">
      <span data-testid="project-features-showing">Showing {{ features.rows.length }} of {{ features.total }}</span>
      <router-link
        :to="featuresTo"
        class="pl-panel__link focus-visible:outline-2 focus-visible:outline-blue-500"
        data-pl-part="panel-link"
        data-testid="project-features-all"
      >All {{ features.total }} in Features →</router-link>
    </footer>
  </section>
</template>

<style scoped>
.px-ps__head { align-items: center; gap: 0.5rem; }
.px-ps__trail { display: flex; align-items: center; gap: 0.5rem; color: var(--pl-muted); }
.px-ps .px-ps__all { height: auto; padding: 0; }
.px-ps__count { margin-left: auto; }

.px-ps__band {
    display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap;
    padding: 1rem 1.1rem; border-bottom: 1px solid var(--pl-frame);
}
.px-ps__progress { flex: 1 1 16rem; min-width: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.px-ps__pct, .px-ps__legend, .px-fig__label, .px-fig__value, .px-ps__table th, .px-ps__stage, .px-ps__tasks,
.px-ps__table .num {
    font-family: var(--pl-font); letter-spacing: 0.08em; text-transform: uppercase;
}
.px-ps__pct { font-size: 10px; color: var(--pl-muted); }
.px-ps__pct b { margin-right: 0.4em; font-size: 14px; font-weight: 400; letter-spacing: 0.08em; color: var(--pl-ship); }

.px-ps__cells { display: flex; flex-wrap: wrap; gap: 3px; min-height: 14px; }
.px-ps__legend { display: flex; flex-wrap: wrap; gap: 0.4rem 1.25rem; font-size: 10px; color: var(--pl-muted); }
.px-ps__legend li { display: inline-flex; align-items: center; gap: 0.4rem; }

/* Stage marks: the one fill a stage wears here (R7), outlined while unstarted.
   Doubled class so it outranks the theme's base meter-cell fill. */
.px-cell { display: inline-block; flex: none; }
.px-cell--stage { width: 14px; height: 14px; }
.px-cell--mark { width: 9px; height: 9px; }
.px-cell--task { width: 8px; height: 10px; }
.px-cell.px-cell[data-tone="spec"] { background: var(--pl-paper); box-shadow: inset 0 0 0 1px var(--pl-muted); }
.px-cell.px-cell[data-tone="building"] { background: var(--pl-px-b); }
.px-cell.px-cell[data-tone="shipped"] { background: var(--pl-px-g); }
.px-cell.px-cell[data-tone="escaped"] { background: var(--pl-px-a); }

:is(.px-ps__legend li, .px-ps__stage).is-building { color: var(--pl-action-text); }
:is(.px-ps__legend li, .px-ps__stage).is-shipped { color: var(--pl-ship); }
:is(.px-ps__legend li, .px-ps__stage).is-escaped { color: var(--pl-warn); }

.px-ps__figs { display: flex; gap: 0.75rem; flex-wrap: wrap; }
.px-fig {
    display: flex; flex-direction: column; justify-content: center; gap: 0.35rem; min-width: 7.5rem;
    padding: 0.5rem 0.9rem; border: 2px solid var(--pl-frame); text-decoration: none; color: var(--pl-text);
}
.px-fig[data-tone="warn"] { border-color: var(--pl-warn-line); }
.px-fig--still { border-color: var(--pl-rule); }
.px-fig__label { font-size: 9px; color: var(--pl-muted); }
.px-fig[data-tone="warn"] .px-fig__label, .px-fig[data-tone="warn"] .px-fig__value { color: var(--pl-warn); }
.px-fig__value { font-size: 16px; font-weight: 400; letter-spacing: 0.08em; text-align: right; font-variant-numeric: tabular-nums; }

.px-ps__table { width: 100%; border-collapse: collapse; font-size: 13px; }
.px-ps__table th {
    padding: 0.6rem 1.1rem; border-bottom: 2px solid var(--pl-frame);
    text-align: left; font-size: 9px; font-weight: 400; color: var(--pl-muted);
}
.px-ps__table td { padding: 0.65rem 1.1rem; border-top: 1px solid var(--pl-hair); color: var(--pl-text); }
.px-ps__table tr:first-child td { border-top: 0; }
.px-ps__table .num { text-align: right; font-size: 10px; font-variant-numeric: tabular-nums; color: var(--pl-muted); }
.px-ps__table .num.is-warn { color: var(--pl-warn); }
.px-ps__title a { color: var(--pl-text); font-weight: 600; text-decoration: none; }
.px-ps__title a:hover { text-decoration: underline; }
.px-ps__id { margin-right: 0.5rem; }
.px-ps__stage { display: inline-flex; align-items: center; gap: 0.5rem; font-size: 10px; color: var(--pl-muted); }
.px-ps__tasks { display: inline-flex; align-items: center; gap: 0.6rem; font-size: 10px; color: var(--pl-muted); white-space: nowrap; }
.px-ps__task-cells { display: inline-flex; gap: 2px; }
.px-ps__empty { margin: 0; }

@media (max-width: 1023.98px) {
    .px-col-tasks, .px-col-active { display: none; }
}
</style>
