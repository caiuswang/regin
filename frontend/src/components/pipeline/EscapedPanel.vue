<script setup>
// "Escaped" (1a): features that shipped and then had bugs blamed on them,
// most recent escape first.
defineProps({
  rows: { type: Array, required: true },
})
</script>

<template>
  <section class="pl-panel es" data-testid="escaped" aria-labelledby="es-title">
    <header class="pl-panel__head">
      <h2 id="es-title" class="pl-panel__title">Escaped</h2>
      <span class="pl-panel__sub">Shipped, then had bugs blamed on it</span>
    </header>
    <p v-if="!rows.length" class="pl-panel__empty" data-testid="escaped-empty">
      Nothing has escaped — no shipped feature has a defect blamed on it.
    </p>
    <ul v-else>
      <li v-for="row in rows" :key="row.feature_id" class="es-row" :data-testid="`escaped-row-${row.feature_id}`">
        <span class="es-id">#{{ row.feature_id }}</span>
        <router-link :to="`/features/${row.feature_id}`" class="es-title focus-visible:outline-2 focus-visible:outline-blue-500">
          {{ row.title }}
        </router-link>
        <span class="es-rounds">{{ row.rounds }} round{{ row.rounds === 1 ? '' : 's' }}</span>
        <span class="es-pill" :class="{ 'is-open': row.defects_open }">
          {{ row.defects_open }} open · {{ row.defects_total }}
        </span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.es-row {
    display: flex; align-items: center; gap: 0.75rem; min-width: 0;
    padding: 0.65rem 1.1rem; border-top: 1px solid var(--color-border-subtle);
}
.es-id { font-size: 12px; color: var(--color-fg-faint); flex: none; }
.es-title {
    flex: 1; min-width: 0; font-size: 13.5px; font-weight: 500; color: var(--color-fg);
    text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.es-title:hover { text-decoration: underline; }
.es-rounds { font-size: 12px; color: var(--color-fg-muted); flex: none; }
.es-pill {
    flex: none; border-radius: 999px; padding: 0.05rem 0.5rem; font-size: 12px; font-weight: 600;
    background: var(--color-surface-3); color: var(--color-fg-muted);
}
.es-pill.is-open { background: var(--color-amber-200); color: var(--color-amber-900); }
</style>
