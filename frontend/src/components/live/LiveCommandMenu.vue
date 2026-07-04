<script setup>
// Floating accept-list for the bridge composer's `/`-autocomplete. Purely
// presentational: LiveComposer owns the state (via useSlashCommands) and hands
// down the filtered items + highlight. Teleported to <body> and fixed-
// positioned from the composer-supplied `anchorStyle`, because the NOW zone
// clips overflow (`.live-now { overflow:hidden }`) — an in-flow upward menu
// would be scissored off and the tail would eat its clicks. `role=listbox`;
// keyboard nav lives in the composer; this emits `select`/`hover`.
import { ref, watch, nextTick } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  activeIndex: { type: Number, default: 0 },
  query: { type: String, default: '' },
  anchorStyle: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['select', 'hover'])

const listEl = ref(null)

// Keep the highlighted row visible as the user arrows through a long list.
watch(() => props.activeIndex, async () => {
  await nextTick()
  const el = listEl.value?.querySelector('[data-highlighted="true"]')
  if (el) el.scrollIntoView({ block: 'nearest' })
})
</script>

<template>
  <Teleport to="body">
  <div
    id="live-command-menu"
    ref="listEl"
    class="live-cmd-menu"
    :style="anchorStyle"
    role="listbox"
    aria-label="Slash commands and skills"
    data-testid="live-command-menu"
  >
    <div v-if="items.length === 0" class="live-cmd-empty" data-testid="live-command-empty">
      no command matches “{{ query }}”
    </div>
    <div
      v-for="(item, i) in items"
      :id="`live-cmd-opt-${i}`"
      :key="`${item.kind}:${item.name}`"
      class="live-cmd-item cursor-pointer hover:bg-[var(--color-surface-2)]"
      role="option"
      :aria-selected="i === activeIndex"
      :data-highlighted="i === activeIndex"
      data-testid="live-command-item"
      @mousedown.prevent="emit('select', item)"
      @mousemove="emit('hover', i)"
    >
      <span class="live-cmd-name">/{{ item.name }}</span>
      <span class="live-cmd-desc">{{ item.description }}</span>
      <span class="live-cmd-kind" :class="`live-cmd-kind-${item.kind}`">{{ item.kind }}</span>
    </div>
  </div>
  </Teleport>
</template>

<style scoped>
.live-cmd-menu {
  position: fixed; /* teleported to body; placed via inline anchorStyle */
  z-index: var(--z-popover);
  max-height: 15rem;
  overflow-y: auto;
  padding: 0.25rem;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18);
}
.live-cmd-empty {
  padding: 0.5rem 0.625rem;
  font-size: 0.8125rem;
  color: var(--color-fg-faint);
}
.live-cmd-item {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  width: 100%;
  padding: 0.3125rem 0.5rem;
  border: 0;
  border-radius: calc(var(--radius-md) - 0.125rem);
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.live-cmd-item[data-highlighted="true"] { background: var(--color-surface-2); }
.live-cmd-name {
  flex: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.8125rem;
  color: var(--color-fg);
}
.live-cmd-desc {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 0.75rem;
  color: var(--color-fg-muted);
}
.live-cmd-kind {
  flex: none;
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 0.0625rem 0.3125rem;
  border-radius: 999px;
  color: var(--color-fg-subtle);
  background: var(--color-surface-3);
}
.live-cmd-kind-skill { color: var(--color-primary); }
</style>
