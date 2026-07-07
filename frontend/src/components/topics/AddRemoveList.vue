<script setup>
/**
 * AddRemoveList — a labelled +added / −removed list, the compare page's
 * unit for reference-file and graph-metadata changes. Parent passes the
 * already-formatted strings, so this stays presentational and generic.
 */
import { computed } from 'vue'

const props = defineProps({
  title: { type: String, default: '' },
  adds: { type: Array, default: () => [] },
  removes: { type: Array, default: () => [] },
  unchangedNote: { type: String, default: '' },
})

const changed = computed(() => props.adds.length > 0 || props.removes.length > 0)
</script>

<template>
  <div v-if="changed || unchangedNote" class="arl">
    <div v-if="title" class="arl__title">{{ title }}</div>
    <p v-if="!changed" class="arl__unchanged">{{ unchangedNote }}</p>
    <ul v-else class="arl__list">
      <li v-for="(item, i) in adds" :key="'a-' + i" class="arl__row arl__row--add">
        <span class="arl__sign" aria-hidden="true">+</span>
        <code class="arl__text">{{ item }}</code>
      </li>
      <li v-for="(item, i) in removes" :key="'r-' + i" class="arl__row arl__row--remove">
        <span class="arl__sign" aria-hidden="true">−</span>
        <code class="arl__text">{{ item }}</code>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.arl { display: flex; flex-direction: column; gap: 0.375rem; }
.arl__title {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-slate-400);
}
.arl__unchanged {
  font-size: 0.75rem;
  color: var(--color-slate-400);
  font-style: italic;
  margin: 0;
}
.arl__list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.125rem; }
.arl__row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.75rem;
  padding: 0.125rem 0.375rem;
  border-radius: 0.375rem;
}
.arl__row--add { background: var(--color-emerald-50); }
.arl__row--remove { background: var(--color-red-50); }
.arl__sign { font-weight: 700; flex-shrink: 0; }
.arl__row--add .arl__sign { color: var(--color-green-700); }
.arl__row--remove .arl__sign { color: var(--color-red-700); }
.arl__text {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.6875rem;
  background: transparent;
  color: var(--color-slate-800);
  word-break: break-word;
}
</style>
