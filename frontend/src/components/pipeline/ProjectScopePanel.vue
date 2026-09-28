<script setup>
// One project picked (1d): the breadcrumb head replaces the Projects panel,
// and the project's five most active features sit under it.
import Button from '../ui/Button.vue'
import SegmentBar from './SegmentBar.vue'
import { ago, loopBar } from '../../utils/pipelineFormat'
import { money } from '../../utils/ledgerFormat'

defineProps({
  head: { type: Object, required: true },
  features: { type: Object, required: true },
  featuresTo: { type: Object, required: true },
})
const emit = defineEmits(['all'])
</script>

<template>
  <section class="pl-panel ps" data-testid="project-scope" aria-labelledby="ps-title">
    <header class="ps-head" data-testid="project-head">
      <div class="ps-crumb">
        <nav class="ps-trail" aria-label="Breadcrumb">
          <Button variant="link" size="sm" class="ps-trail__all" data-testid="project-head-all" @click="emit('all')">
            {{ head.breadcrumb[0] }}
          </Button>
          <span aria-hidden="true">/</span>
        </nav>
        <h2 id="ps-title" class="ps-name">{{ head.breadcrumb[1] }}</h2>
      </div>
      <div class="ps-progress">
        <SegmentBar :parts="loopBar(head.stage_bar)" :height="6" />
        <span class="ps-pct">{{ head.shipped_pct == null ? 'nothing live' : `${head.shipped_pct}% shipped` }}</span>
      </div>
      <dl class="ps-figs">
        <div><dt>open tasks</dt><dd>{{ head.open_tasks }}</dd></div>
        <div :class="{ 'is-warn': head.open_defects }"><dt>open defects</dt><dd>{{ head.open_defects }}</dd></div>
        <div><dt>spent</dt><dd>{{ head.spent_usd == null ? '—' : money(head.spent_usd) }}</dd></div>
      </dl>
    </header>

    <table class="ps-table" data-testid="project-features">
      <thead>
        <tr>
          <th scope="col">Features in this project</th>
          <th scope="col">Stage</th>
          <th scope="col">Tasks</th>
          <th scope="col" class="num">Defects</th>
          <th scope="col" class="num">Active</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!features.rows.length">
          <td colspan="5" class="ps-empty">No feature is filed under this project yet.</td>
        </tr>
        <tr v-for="f in features.rows" :key="f.id" :data-testid="`project-feature-${f.id}`">
          <td class="ps-title">
            <span class="ps-id">#{{ f.id }}</span>
            <router-link :to="`/features/${f.id}`" class="focus-visible:outline-2 focus-visible:outline-blue-500">{{ f.title }}</router-link>
          </td>
          <td>
            <span class="ps-stage" :class="{ 'is-warn': f.stage === 'escaped' }">{{ f.stage }}</span>
          </td>
          <td>
            <span class="ps-tasks">
              <span class="ps-track"><span class="ps-fill tone-settled" :style="{ width: `${f.tasks ? (100 * f.tasks_done) / f.tasks : 0}%` }" /></span>
              {{ f.tasks_done }} / {{ f.tasks }}
            </span>
          </td>
          <td class="num" :class="{ 'is-warn': f.defects.open }">{{ f.defects.open }} / {{ f.defects.total }}</td>
          <td class="num muted">{{ ago(f.last_activity) }}</td>
        </tr>
      </tbody>
    </table>
    <footer class="pl-panel__foot">
      <span>Showing {{ features.rows.length }} of {{ features.total }}</span>
      <router-link :to="featuresTo" class="pl-panel__link focus-visible:outline-2 focus-visible:outline-blue-500"
                   data-testid="project-features-all"
      >All {{ features.total }} in Features →</router-link>
    </footer>
  </section>
</template>

<style scoped>
.ps-head {
    display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap;
    padding: 0.9rem 1.1rem; border-bottom: 1px solid var(--color-border-subtle);
}
.ps-crumb { min-width: 0; }
.ps-trail { display: flex; align-items: center; gap: 0.3rem; font-size: 12px; color: var(--color-fg-faint); }
.ps-trail .ps-trail__all { font-size: 12px; color: var(--color-fg-muted); }
.ps-name { font-size: 17px; font-weight: 700; color: var(--color-fg); }
.ps-progress { flex: 1 1 12rem; min-width: 10rem; display: flex; flex-direction: column; gap: 0.25rem; align-items: flex-end; }
.ps-pct { font-size: 12px; font-weight: 600; color: var(--color-fg); }
.ps-figs { display: flex; gap: 1.1rem; font-size: 12.5px; }
.ps-figs div { display: flex; flex-direction: row-reverse; gap: 0.3rem; align-items: baseline; }
.ps-figs dt { color: var(--color-fg-muted); }
.ps-figs dd { font-weight: 700; color: var(--color-fg); }
.ps-figs .is-warn dd, .ps-figs .is-warn dt { color: var(--color-warning-strong); }
.ps-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.ps-table th {
    text-align: left; padding: 0.55rem 1.1rem; font-size: 10.5px; font-weight: 600;
    letter-spacing: 0.06em; text-transform: uppercase; color: var(--color-fg-faint);
}
.ps-table td { padding: 0.55rem 1.1rem; border-top: 1px solid var(--color-border-subtle); }
.ps-table .num { text-align: right; font-variant-numeric: tabular-nums; }
.ps-table .muted { color: var(--color-fg-muted); }
.ps-table .is-warn { color: var(--color-warning-strong); font-weight: 600; }
.ps-title a { color: var(--color-fg); text-decoration: none; font-weight: 500; }
.ps-title a:hover { text-decoration: underline; }
.ps-id { margin-right: 0.4rem; font-size: 12px; color: var(--color-fg-faint); }
.ps-stage { font-size: 12.5px; color: var(--color-fg-muted); }
.ps-tasks { display: inline-flex; align-items: center; gap: 0.6rem; font-variant-numeric: tabular-nums; }
.ps-track { width: 7rem; height: 4px; border-radius: 999px; background: var(--color-border); overflow: hidden; }
.ps-fill { display: block; height: 100%; }
.ps-empty { color: var(--color-fg-muted); }

@media (max-width: 1023.98px) {
    .ps-table th:nth-child(3), .ps-table td:nth-child(3),
    .ps-table th:nth-child(5), .ps-table td:nth-child(5) { display: none; }
}
</style>
