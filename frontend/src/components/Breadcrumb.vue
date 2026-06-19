<script setup>
defineProps({
  items: { type: Array, required: true },
  // items: [{ label: 'Rules', to: '/rules' }, { label: 'entity_checks', to: null }]
})
</script>

<template>
  <nav class="breadcrumb" aria-label="Breadcrumb">
    <template v-for="(item, pos) in items" :key="`${item.to ?? ''}|${item.label}`">
      <svg v-if="pos > 0" class="breadcrumb-sep" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>
      <router-link
        v-if="item.to"
        :to="item.to"
        class="breadcrumb-link focus-visible:outline-2 focus-visible:outline-blue-500"
      >{{ item.label }}</router-link>
      <span v-else class="breadcrumb-current">{{ item.label }}</span>
    </template>
  </nav>
</template>

<style scoped>
.breadcrumb {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.75rem;
    color: var(--color-slate-400);
    margin-bottom: 0.75rem;
    min-width: 0;
}

.breadcrumb-sep { color: var(--color-slate-300); flex-shrink: 0; }

.breadcrumb-link {
    color: var(--color-slate-500);
    text-decoration: none;
    transition: color 150ms;
    border-radius: 4px;
    padding: 0.0625rem 0.25rem;
    margin: 0 -0.25rem;
}
.breadcrumb-link:hover { color: var(--color-slate-900); }

.breadcrumb-current {
    color: var(--color-slate-900);
    font-weight: 500;
}
</style>
