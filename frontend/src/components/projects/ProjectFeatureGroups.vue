<script setup>
// A project's features, grouped by stage — Linear's issue list under a
// project, with the stage standing in for workflow state. Work in flight
// leads; what is settled trails. `sort=stage` server-side keeps each group in
// one pass of the page, so this only buckets — it never re-orders.
import { computed } from 'vue'
import Badge from '../Badge.vue'
import { STAGE_COLOR } from '../../utils/ledgerFormat'

const ORDER = ['building', 'escaped', 'spec', 'shipped', 'abandoned']

const props = defineProps({
  features: { type: Object, required: true },
  scope: { type: String, required: true },
})

const groups = computed(() => ORDER
  .map(stage => ({ stage, rows: props.features.items.filter(f => f.stage === stage) }))
  .filter(g => g.rows.length))
</script>

<template>
  <section class="pfg" data-testid="project-features">
    <p v-if="!features.items.length" class="empty-state" data-testid="project-features-empty">
      No features are filed under this project yet.
    </p>
    <div v-for="g in groups" :key="g.stage" class="pfg__group" :data-testid="`project-stage-${g.stage}`">
      <h3 class="pfg__head">
        <Badge :color="STAGE_COLOR[g.stage] || 'gray'" :label="g.stage" />
        <span class="pfg__count">{{ g.rows.length }}</span>
      </h3>
      <ul class="pfg__list">
        <li v-for="f in g.rows" :key="f.id" class="pfg__row">
          <router-link
            :to="`/features/${f.id}`"
            class="table-link pfg__title focus-visible:outline-2 focus-visible:outline-blue-500"
          >
            <span class="pfg__id">#{{ f.id }}</span> {{ f.title }}
          </router-link>
          <span class="pfg__meta">
            <span v-if="f.tasks">{{ f.tasks }} task{{ f.tasks === 1 ? '' : 's' }}</span>
            <span v-if="f.defects.open" class="pfg__hot">{{ f.defects.open }} open defect{{ f.defects.open === 1 ? '' : 's' }}</span>
            <span v-if="f.rounds > 1">round {{ f.rounds }}</span>
          </span>
        </li>
      </ul>
    </div>
    <p v-if="features.truncated" class="pfg__more" data-testid="project-features-truncated">
      Showing {{ features.items.length }} of {{ features.matched }} —
      <router-link :to="{ path: '/features', query: { project: scope } }">every feature in this project</router-link>
    </p>
  </section>
</template>

<style scoped>
.pfg { display: flex; flex-direction: column; gap: 1rem; }
.pfg__head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0 0 0.375rem;
    font-size: 0.8125rem;
}
.pfg__count { color: var(--color-text-muted, #6b7280); font-variant-numeric: tabular-nums; }
.pfg__list { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--color-border, #e5e7eb); }
.pfg__row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.4375rem 0.25rem;
    border-bottom: 1px solid var(--color-border, #e5e7eb);
    min-width: 0;
}
.pfg__title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pfg__id { color: var(--color-text-muted, #6b7280); font-variant-numeric: tabular-nums; margin-right: 0.25rem; }
.pfg__meta {
    display: flex;
    gap: 0.625rem;
    flex-shrink: 0;
    font-size: 0.75rem;
    color: var(--color-text-muted, #6b7280);
}
.pfg__hot { color: var(--color-amber-700, #b45309); }
.pfg__more { font-size: 0.8125rem; color: var(--color-text-muted, #6b7280); }
</style>
