<script setup>
// Dropdown/kebab menu on Reka: roving-focus keyboard nav, click-outside,
// Escape, and proper role=menu/menuitem semantics for free. Pass `items`
// as { label, onSelect, danger, disabled } or { separator: true }.
import { DropdownMenuRoot, DropdownMenuTrigger, DropdownMenuPortal, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator } from 'reka-ui'

defineProps({
  items: { type: Array, default: () => [] },
  align: { type: String, default: 'end' },
})
</script>

<template>
  <DropdownMenuRoot>
    <DropdownMenuTrigger as-child>
      <slot name="trigger" />
    </DropdownMenuTrigger>
    <DropdownMenuPortal>
      <DropdownMenuContent :align="align" :side-offset="6" class="ds-menu">
        <template v-for="(item, i) in items" :key="i">
          <DropdownMenuSeparator v-if="item.separator" class="ds-menu-sep" />
          <DropdownMenuItem
            v-else
            class="ds-menu-item"
            :class="{ 'ds-menu-item-danger': item.danger }"
            :disabled="item.disabled"
            @select="item.onSelect && item.onSelect()"
          >
            {{ item.label }}
          </DropdownMenuItem>
        </template>
      </DropdownMenuContent>
    </DropdownMenuPortal>
  </DropdownMenuRoot>
</template>

<!-- NOT scoped: Reka portals this content to <body>, where Vue's scoped
     data-v attribute does not reach it. Class names are ds-* prefixed. -->
<style>
.ds-menu {
  z-index: var(--z-dropdown);
  min-width: 10rem;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--ds-shadow-lg);
  padding: 0.25rem;
}
.ds-menu-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4375rem 0.625rem;
  font-size: 0.8125rem;
  color: var(--color-fg);
  border-radius: var(--radius-md);
  cursor: pointer;
  outline: none;
  user-select: none;
}
.ds-menu-item[data-highlighted] { background: var(--color-surface-2); }
.ds-menu-item[data-disabled] { opacity: 0.5; cursor: not-allowed; }
.ds-menu-item-danger { color: var(--color-danger); }
.ds-menu-item-danger[data-highlighted] { background: var(--color-danger-soft); }
.ds-menu-sep { height: 1px; background: var(--color-border-subtle); margin: 0.25rem 0; }
</style>
