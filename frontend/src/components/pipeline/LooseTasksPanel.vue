<script setup>
// "No feature" (F171): open tasks filed under no feature. Every other Overview
// route to open work goes through a feature or an armed/running state, so an
// idea nobody cut a feature for is reachable only from here.
import { computed } from 'vue'

const props = defineProps({
  loose: { type: Object, required: true },
  tasksTo: { type: Object, required: true },
})

const allTo = computed(() => ({
  ...props.tasksTo, query: { ...props.tasksTo.query, unfeatured: '1' },
}))
</script>

<template>
  <section class="pl-panel lt" data-pl-part="panel" data-testid="loose-tasks" aria-labelledby="lt-title">
    <header class="pl-panel__head" data-pl-part="panel-head">
      <h2 id="lt-title" class="pl-panel__title" data-pl-part="panel-title">No feature</h2>
      <span class="pl-panel__sub" data-pl-part="panel-meta" data-pl-label="open" data-testid="loose-tasks-count">{{ loose.total }} open</span>
      <router-link
        v-if="loose.total > loose.rows.length"
        :to="allTo"
        class="pl-panel__link focus-visible:outline-2 focus-visible:outline-blue-500"
        data-pl-part="panel-link"
        data-testid="loose-tasks-all"
      >View all {{ loose.total }} →</router-link>
    </header>
    <ul class="lt-rows">
      <li v-for="row in loose.rows" :key="row.id" class="lt-row" data-pl-part="row" :data-testid="`loose-tasks-row-${row.id}`">
        <span class="lt-id" data-pl-part="row-id">#{{ row.id }}</span>
        <router-link :to="`/tasks/${row.id}`" class="lt-title focus-visible:outline-2 focus-visible:outline-blue-500" data-pl-part="row-title">
          {{ row.title }}
        </router-link>
        <span class="lt-status" :class="`is-${row.status}`">{{ row.status }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.lt-rows { display: flex; flex-direction: column; }
.lt-row {
    display: grid; grid-template-columns: auto minmax(0, 1fr) auto; column-gap: 0.75rem;
    align-items: baseline; padding: 0.6rem 1.1rem; border-top: 1px solid var(--color-border-subtle);
}
.lt-id { font-size: 12px; color: var(--color-fg-faint); font-variant-numeric: tabular-nums; }
.lt-title {
    min-width: 0; font-size: 13px; font-weight: 500; color: var(--color-fg); text-decoration: none;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.lt-title:hover { text-decoration: underline; }
.lt-status { font-size: 12px; color: var(--color-fg-muted); }
.lt-status.is-running { color: var(--color-fg); font-weight: 600; }
.lt-status.is-failed { color: var(--color-warning-strong); font-weight: 600; }
</style>
