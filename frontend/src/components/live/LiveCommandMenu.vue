<script setup>
// Floating accept-list for the bridge composer's `/`-autocomplete. Purely
// presentational: LiveComposer owns the state (via useSlashCommands /
// useFileMentions) and hands down the filtered items + highlight. Teleported to
// <body> and fixed-positioned from the composer-supplied `anchorStyle`, because
// the NOW zone clips overflow (`.live-now { overflow:hidden }`) — an in-flow
// upward menu would be scissored off and the tail would eat its clicks.
// `role=listbox`; keyboard nav lives in the composer; this emits
// `select`/`hover`.
//
// One component serves both trigger modes: `prefix` labels the row (`/` or
// `@`), and a row carrying a file/directory `kind` swaps the kind pill for the
// matching icon. `loading` is distinct from an empty list on purpose — the
// catalog handshake is slow enough that "no match" during it would be a lie.
import { ref, watch, nextTick } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  activeIndex: { type: Number, default: 0 },
  query: { type: String, default: '' },
  anchorStyle: { type: Object, default: () => ({}) },
  prefix: { type: String, default: '/' },
  ariaLabel: { type: String, default: 'Slash commands and skills' },
  emptyText: { type: String, default: 'no command matches' },
  loading: { type: Boolean, default: false },
  // The rows on screen answer a superseded query, so an accept will be refused
  // until the live one lands: dim them, or a click that does nothing looks like
  // a broken menu instead of a busy one.
  stale: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'hover'])

const listEl = ref(null)

const rowName = (item) => item.name ?? item.path ?? ''
const fileIcon = (item) => (
  item.kind === 'directory' ? 'folder' : item.kind === 'file' ? 'file' : '')

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
    :class="{ 'live-cmd-menu-stale': stale }"
    :style="anchorStyle"
    role="listbox"
    :aria-label="ariaLabel"
    data-testid="live-command-menu"
  >
    <div v-if="loading" class="live-cmd-loading" data-testid="live-command-loading">
      <span class="live-spinner live-spinner-sm" aria-hidden="true"></span>
      <span>loading…</span>
    </div>
    <div
      v-else-if="items.length === 0"
      class="live-cmd-empty"
      data-testid="live-command-empty"
    >
      {{ emptyText }} “{{ query }}”
    </div>
    <div
      v-for="(item, i) in items"
      :id="`live-cmd-opt-${i}`"
      :key="`${item.kind}:${rowName(item)}`"
      class="live-cmd-item cursor-pointer hover:bg-[var(--color-surface-2)]"
      role="option"
      :aria-selected="i === activeIndex"
      :data-highlighted="i === activeIndex"
      data-testid="live-command-item"
      @mousedown.prevent="emit('select', item)"
      @mousemove="emit('hover', i)"
    >
      <Icon v-if="fileIcon(item)" :name="fileIcon(item)" :size="13" class="live-cmd-icon" />
      <span
        class="live-cmd-name"
        :class="{ 'live-cmd-name-path': fileIcon(item) }"
      >{{ prefix }}{{ rowName(item) }}</span>
      <span v-if="item.argumentHint" class="live-cmd-hint">{{ item.argumentHint }}</span>
      <span class="live-cmd-desc">{{ item.description }}</span>
      <span
        v-if="item.risk === 'destructive'"
        class="live-cmd-kind live-cmd-risk"
        data-testid="live-command-risk"
      >{{ item.risk }}</span>
      <span
        v-if="!fileIcon(item) && item.kind"
        class="live-cmd-kind"
        :class="`live-cmd-kind-${item.kind}`"
      >{{ item.kind }}</span>
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
.live-cmd-empty,
.live-cmd-loading {
  padding: 0.5rem 0.625rem;
  font-size: 0.8125rem;
  color: var(--color-fg-faint);
}
.live-cmd-loading {
  display: flex;
  align-items: center;
  gap: 0.375rem;
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
.live-cmd-menu-stale .live-cmd-item {
  opacity: 0.45;
  cursor: progress;
}
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
.live-cmd-risk {
  color: var(--color-warning-strong);
  background: var(--color-warning-soft);
  font-weight: 600;
}
.live-cmd-hint {
  flex: none;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  color: var(--color-fg-faint);
}
/* Only a mention label is shrinkable: it carries a full repo-relative path,
   which can outrun the 375px composer width a command name never does. */
.live-cmd-name-path {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.live-cmd-icon {
  flex: none;
  align-self: center;
  color: var(--color-fg-subtle);
}
</style>
