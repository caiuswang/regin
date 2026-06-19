<script setup>
// One tab bar to replace the 7 ad-hoc implementations the audit found.
// Reka TabsRoot gives roving-focus + arrow-key nav + role=tab/tablist.
// `variant`: 'segmented' (pill group) or 'underline' (border-bottom).
// v-model the active value; parent renders the panel for that value.
import { TabsRoot, TabsList, TabsTrigger } from 'reka-ui'

const model = defineModel({ type: [String, Number], default: '' })
defineProps({
  tabs: { type: Array, default: () => [] },
  variant: { type: String, default: 'segmented' },
})
</script>

<template>
  <TabsRoot v-model="model" :class="['ds-tabs', `ds-tabs-${variant}`]">
    <TabsList class="ds-tablist">
      <TabsTrigger
        v-for="t in tabs"
        :key="String(t.value ?? t)"
        :value="t.value ?? t"
        class="ds-tab"
      >
        {{ t.label ?? t }}
      </TabsTrigger>
    </TabsList>
  </TabsRoot>
</template>

<style scoped>
.ds-tablist { display: inline-flex; align-items: center; }

/* Segmented (pill group) */
.ds-tabs-segmented .ds-tablist {
  gap: 0.25rem;
  background: var(--color-surface-2);
  padding: 0.25rem;
  border-radius: var(--radius-lg);
}
.ds-tabs-segmented .ds-tab {
  padding: 0.375rem 1rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-fg-muted);
  background: transparent;
  border: 0;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color 150ms, color 150ms;
}
.ds-tabs-segmented .ds-tab:hover { color: var(--color-fg); }
.ds-tabs-segmented .ds-tab[data-state='active'] {
  background: var(--color-surface);
  color: var(--color-primary-active);
  box-shadow: var(--ds-shadow-sm);
}

/* Underline (border-bottom) */
.ds-tabs-underline .ds-tablist {
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border);
}
.ds-tabs-underline .ds-tab {
  padding: 0.5rem 0.9rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--color-fg-subtle);
  background: transparent;
  border: 0;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  cursor: pointer;
  transition: color 150ms, border-color 150ms;
}
.ds-tabs-underline .ds-tab:hover { color: var(--color-fg); }
.ds-tabs-underline .ds-tab[data-state='active'] {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}

.ds-tab:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
</style>
