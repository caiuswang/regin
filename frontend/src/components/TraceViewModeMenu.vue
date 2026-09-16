<script setup>
// The view-mode picker for the trace pane, as a menu rather than a segmented
// strip. The four modes are one exclusive choice, and laid out as segments
// they spent ~390px of the header's busiest row showing three labels the
// reader is not currently in — width the row now spends on session facts.
// Reka's radio-group menu also states "one of four" more plainly than four
// `aria-pressed` buttons did.
import { computed } from 'vue'
import {
  DropdownMenuRoot,
  DropdownMenuTrigger,
  DropdownMenuPortal,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuItemIndicator,
} from 'reka-ui'
import Button from './ui/Button.vue'
import Icon from './ui/Icon.vue'

const props = defineProps({
  // [{ id, label }] — the exclusive set of modes.
  modes: { type: Array, required: true },
  viewMode: { type: String, required: true },
  // Per-mode row counts, e.g. { terminal: 38 }. A missing or zero entry
  // renders no count.
  counts: { type: Object, default: () => ({}) },
})
defineEmits(['update:viewMode'])

const current = computed(
  () => props.modes.find((m) => m.id === props.viewMode) || props.modes[0],
)
</script>

<template>
  <DropdownMenuRoot>
    <DropdownMenuTrigger as-child>
      <Button
        variant="ghost"
        size="sm"
        data-testid="view-mode-menu"
        class="h-auto shrink-0 gap-1.5 border border-slate-200 bg-white px-2.5 py-1 text-[12px] font-semibold text-slate-700 hover:border-slate-300 hover:bg-slate-50"
        :title="`View mode — showing ${current.label}. Opens the other panes.`"
      >{{ current.label }}<!-- aria-hidden: the count is a density hint, and
        folding it into the accessible name would make the trigger's name a
        moving target ("Terminal" → "Terminal 38"). --><span
        v-if="counts[current.id]"
        aria-hidden="true"
        class="rounded-md bg-slate-100 px-1.5 text-[10.5px] font-semibold tabular-nums text-slate-500"
      >{{ counts[current.id] }}</span><Icon
        name="chevron-down"
        size="13"
        class="text-slate-400"
      /></Button>
    </DropdownMenuTrigger>
    <DropdownMenuPortal>
      <DropdownMenuContent align="end" :side-offset="6" class="ds-mode-menu">
        <DropdownMenuRadioGroup
          :model-value="viewMode"
          @update:model-value="$emit('update:viewMode', $event)"
        >
          <DropdownMenuRadioItem
            v-for="m in modes"
            :key="m.id"
            :value="m.id"
            class="ds-mode-item"
          >
            <span class="ds-mode-check">
              <DropdownMenuItemIndicator>
                <Icon name="check" size="13" />
              </DropdownMenuItemIndicator>
            </span>
            <span class="ds-mode-label">{{ m.label }}</span>
            <!-- aria-hidden for the same reason as the trigger's: the count
                 moves under the reader, and the mode's name should not. -->
            <span
              v-if="counts[m.id]"
              aria-hidden="true"
              class="ds-mode-count"
            >{{ counts[m.id] }}</span>
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenuPortal>
  </DropdownMenuRoot>
</template>

<!-- NOT scoped: Reka portals the content to <body>, where this component's
     data-v attribute does not reach it. Class names are ds-* prefixed. -->
<style>
.ds-mode-menu {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--ds-shadow-lg);
  min-width: 11rem;
  padding: 0.25rem;
  z-index: var(--z-dropdown);
}
.ds-mode-item {
  align-items: center;
  border-radius: var(--radius-md);
  color: var(--color-fg);
  cursor: pointer;
  display: flex;
  font-size: 0.8125rem;
  gap: 0.5rem;
  outline: none;
  padding: 0.4375rem 0.625rem;
  user-select: none;
}
.ds-mode-item[data-highlighted] { background: var(--color-surface-selected); }
.ds-mode-item[data-state='checked'] { color: var(--color-info-strong); font-weight: 600; }
.ds-mode-check {
  align-items: center;
  display: inline-flex;
  justify-content: center;
  width: 0.8125rem;
}
.ds-mode-label { flex: 1 1 auto; }
.ds-mode-count {
  background: var(--color-surface-3);
  border-radius: var(--radius-md);
  color: var(--color-fg-subtle);
  font-size: 0.65625rem;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  padding: 0 0.375rem;
}
</style>
