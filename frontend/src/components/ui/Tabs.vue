<script setup>
// One tab bar to replace the 7 ad-hoc implementations the audit found.
// Reka TabsRoot gives roving-focus + arrow-key nav + role=tab/tablist.
// `variant`: 'segmented' (pill group) or 'underline' (border-bottom).
// v-model the active value; parent renders the panel for that value.
import { ref, watch, nextTick, onMounted } from 'vue'
import { TabsRoot, TabsList, TabsTrigger } from 'reka-ui'

const model = defineModel({ type: [String, Number], default: '' })
defineProps({
  tabs: { type: Array, default: () => [] },
  variant: { type: String, default: 'segmented' },
})

const listRef = ref(null)
const fade = ref(false)

function listEl() {
  return listRef.value?.$el
}

function syncFade() {
  const el = listEl()
  if (!el) return
  fade.value = el.scrollWidth > el.clientWidth
    && el.scrollLeft + el.clientWidth < el.scrollWidth - 1
}

// Horizontal reveal only — scrollIntoView({block:'nearest'}) would also
// scroll ancestor containers vertically when the tablist sits below the
// fold (e.g. tabs inside an expansion row).
async function revealActive() {
  await nextTick()
  const el = listEl()
  const active = el?.querySelector('[data-state="active"]')
  if (el && active && el.scrollWidth > el.clientWidth) {
    const listRect = el.getBoundingClientRect()
    const rect = active.getBoundingClientRect()
    if (rect.left < listRect.left) el.scrollLeft += rect.left - listRect.left - 8
    else if (rect.right > listRect.right) el.scrollLeft += rect.right - listRect.right + 8
  }
  syncFade()
}

onMounted(revealActive)
watch(model, revealActive)
</script>

<template>
  <TabsRoot v-model="model" :class="['ds-tabs', `ds-tabs-${variant}`]">
    <TabsList
      ref="listRef"
      class="ds-tablist"
      :class="{ 'ds-tablist-fade': fade }"
      @scroll.passive="syncFade"
    >
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
/* Scroll the tab bar horizontally when it outgrows a narrow (mobile)
   container rather than pushing the whole page sideways. */
.ds-tablist { display: inline-flex; align-items: center; max-width: 100%; overflow-x: auto; }
.ds-tab { flex-shrink: 0; white-space: nowrap; }

/* Right-edge fade signalling more tabs off-screen; applied only while the
   list actually overflows and isn't scrolled to its end. */
.ds-tablist-fade {
  -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 2rem), transparent);
  mask-image: linear-gradient(to right, #000 calc(100% - 2rem), transparent);
}

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
