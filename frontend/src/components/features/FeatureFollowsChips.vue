<script setup>
// The earlier phases an index row follows, one chip each, each a link there.
defineProps({
  ids: { type: Array, default: () => [] },
})
</script>

<template>
  <span v-if="ids.length" class="ffc">
    <router-link
      v-for="id in ids"
      :key="id"
      :to="`/features/${id}`"
      class="ffc-chip focus-visible:outline-2 focus-visible:outline-blue-500"
      :data-testid="`feature-follows-${id}`"
      @click.stop
    >follows #{{ id }}</router-link>
  </span>
</template>

<style scoped>
.ffc { display: inline-flex; gap: 0.25rem; flex-wrap: wrap; }
.ffc-chip {
    display: inline-flex; align-items: center;
    padding: 0 0.4rem; border-radius: 999px;
    border: 1px solid var(--color-border);
    font-size: 11px; line-height: 18px; color: var(--color-fg-muted);
    text-decoration: none; white-space: nowrap;
}
.ffc-chip:hover { color: var(--color-fg); background: var(--color-surface-2); }
</style>
