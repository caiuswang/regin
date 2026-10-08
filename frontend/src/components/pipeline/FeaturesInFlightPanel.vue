<script setup>
// "Features" (F148 A): the features still being specced or built, most
// recently active first — the loop's first stage, ahead of the tasks it cuts.
import { ago } from '../../utils/pipelineFormat'

defineProps({
  flight: { type: Object, required: true },
  parts: { type: Object, required: true },
  featuresTo: { type: Function, required: true },
})

const STAGE_LINKS = [{ stage: 'spec', label: 'spec' }, { stage: 'building', label: 'building' }]
</script>

<template>
  <section class="pl-panel fif" data-pl-part="panel" data-testid="features-in-flight" aria-labelledby="fif-title">
    <header class="pl-panel__head" data-pl-part="panel-head">
      <h2 id="fif-title" class="pl-panel__title" data-pl-part="panel-title">Features</h2>
      <span class="pl-panel__sub" data-pl-part="panel-meta" data-pl-label="in flight" data-testid="features-in-flight-count">{{ flight.total }}</span>
      <router-link
        v-for="link in STAGE_LINKS"
        :key="link.stage"
        :to="featuresTo(null, link.stage)"
        class="pl-panel__sub fif-link focus-visible:outline-2 focus-visible:outline-blue-500"
        data-pl-part="panel-meta"
        :data-testid="`features-in-flight-${link.stage}`"
      >· {{ parts[link.stage] || 0 }} {{ link.label }}</router-link>
      <router-link :to="featuresTo()" class="pl-panel__link focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="panel-link"
                   data-testid="features-in-flight-all"
      >View all →</router-link>
    </header>
    <p v-if="!flight.rows.length" class="pl-panel__empty" data-pl-part="panel-empty" data-testid="features-in-flight-empty">
      No feature is being specced or built.
    </p>
    <ul v-else class="fif-rows">
      <li v-for="row in flight.rows" :key="row.id" class="fif-row" data-pl-part="row" :data-testid="`features-in-flight-row-${row.id}`">
        <span class="fif-id" data-pl-part="row-id">#{{ row.id }}</span>
        <router-link :to="`/features/${row.id}`" class="fif-title focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="row-title">
          {{ row.title }}
        </router-link>
        <span class="fif-stage" :class="`is-${row.stage}`" data-testid="features-in-flight-stage">{{ row.stage }}</span>
        <span class="fif-ago">{{ ago(row.last_activity_at) }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.fif-rows { display: flex; flex-direction: column; }
.fif-row {
    display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; column-gap: 0.75rem;
    align-items: baseline; padding: 0.6rem 1.1rem; border-top: 1px solid var(--color-border-subtle);
}
.fif-id { font-size: 12px; color: var(--color-fg-faint); font-variant-numeric: tabular-nums; }
.fif-title {
    min-width: 0; font-size: 13px; font-weight: 500; color: var(--color-fg); text-decoration: none;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.fif-title:hover, .fif-link:hover { text-decoration: underline; }
.fif-link { color: inherit; text-decoration: none; }
.fif-stage { font-size: 12px; color: var(--color-fg-muted); }
.fif-stage.is-building { color: var(--color-fg); font-weight: 600; }
.fif-ago { font-size: 11.5px; color: var(--color-fg-faint); white-space: nowrap; }
</style>
