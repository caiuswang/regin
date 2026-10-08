<script setup>
// One project picked (Overview, under a scope): the breadcrumb head, the
// project's stages as one cell per feature and its three figures as keys. Same
// props, emits and links as the classic panel.
import { computed } from 'vue'
import Button from '../../../../components/ui/Button.vue'
import { meterCells, METER_CELLS } from '../meter.js'
import { STAGES } from '../../../../utils/pipelineFormat'
import { money } from '../../../../utils/ledgerFormat'

const props = defineProps({
  head: { type: Object, required: true },
  featuresTo: { type: Object, required: true },
})
const emit = defineEmits(['all'])

// Settled work first, so the row fills left to right like a progress bar.
const CELL_ORDER = ['shipped', 'escaped', 'building', 'spec']

const legend = computed(() => STAGES.map(stage => ({ stage, n: Number(props.head.stage_bar?.[stage] || 0) })))

// One cell per live feature while that stays countable; past a meter's width
// the cells become shares, as the loop's meters are.
const stageCells = computed(() => {
  const parts = CELL_ORDER.map(stage => ({ n: Number(props.head.stage_bar?.[stage] || 0), tone: stage }))
  const total = parts.reduce((n, p) => n + p.n, 0)
  return meterCells(parts, Math.min(total, METER_CELLS))
})

const liveFeatures = computed(() => legend.value.reduce((n, l) => n + l.n, 0))
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
        <span class="pl-panel__sub px-ps__count" data-pl-part="panel-meta" data-testid="project-head-count">{{ liveFeatures }} features</span>
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

  </section>
</template>

<style scoped>
.px-ps__head { align-items: center; gap: 0.5rem; }
.px-ps__trail { display: flex; align-items: center; gap: 0.5rem; color: var(--pl-muted); }
.px-ps .px-ps__all { height: auto; padding: 0; }
.px-ps__count { margin-left: auto; }

.px-ps__band {
    display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap;
    padding: 1rem 1.1rem;
}
.px-ps__progress { flex: 1 1 16rem; min-width: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.px-ps__pct, .px-ps__legend, .px-fig__label, .px-fig__value {
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
.px-cell.px-cell[data-tone="spec"] { background: var(--pl-paper); box-shadow: inset 0 0 0 1px var(--pl-muted); }
.px-cell.px-cell[data-tone="building"] { background: var(--pl-px-b); }
.px-cell.px-cell[data-tone="shipped"] { background: var(--pl-px-g); }
.px-cell.px-cell[data-tone="escaped"] { background: var(--pl-px-a); }

.px-ps__legend li.is-building { color: var(--pl-action-text); }
.px-ps__legend li.is-shipped { color: var(--pl-ship); }
.px-ps__legend li.is-escaped { color: var(--pl-warn); }

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
</style>
