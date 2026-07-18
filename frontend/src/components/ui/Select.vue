<script setup>
// Unified single-select on Reka: a token-styled trigger + a portalled,
// fully-styleable listbox — replacing the native <select> whose OS dropdown
// couldn't be themed. Keyboard nav, typeahead, roving focus, click-outside
// and Escape come from Reka; the value round-trips as its original type.
// Drop-in API (modelValue / options / placeholder / disabled / block / class),
// so the 40+ call sites need no change. Pass `options` as strings,
// { value, label, disabled, count }, or { separator: true } to rule a group.
import { computed } from 'vue'
import {
  SelectRoot, SelectTrigger, SelectIcon, SelectPortal,
  SelectContent, SelectViewport, SelectItem, SelectItemIndicator, SelectItemText,
  SelectSeparator,
} from 'reka-ui'
import { cn } from '../../utils/cn'

// Fallthrough attrs (aria-label, id, name, …) belong on the trigger, not the root.
defineOptions({ inheritAttrs: false })

const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: '' },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  // Default: size to the widest option (capped at the container) so a bare
  // <Select> never stretches ugly-wide in a toolbar. `block` fills the column.
  block: { type: Boolean, default: false },
  // Strip the trigger's own border/background/ring so it drops into external
  // chrome that owns those (e.g. the SessionsView facet pill).
  bare: { type: Boolean, default: false },
  class: { type: null, default: '' },
})
const emit = defineEmits(['update:modelValue'])

// Reka forbids an empty-string SelectItem value (it reserves "" as its own
// "clear the selection" sentinel and throws on it), yet many call sites use
// `{ value: '', label: 'All …' }` as a real option. Bridge it: swap "" for a
// private token across the Reka boundary, and swap it back on emit so callers
// still send/receive "".
const EMPTY = ' ds-empty'
const toReka = (v) => (v === '' ? EMPTY : v)

const items = computed(() =>
  props.options.map((o) => {
    if (o !== null && typeof o === 'object' && o.separator) return { separator: true }
    return o !== null && typeof o === 'object'
      ? { value: o.value, label: o.label ?? String(o.value), disabled: !!o.disabled, count: o.count }
      : { value: o, label: String(o), disabled: false, count: undefined }
  }),
)

const model = computed({
  // null / undefined = unset (Reka shows the placeholder); "" is a real value.
  get: () => (props.modelValue == null ? undefined : toReka(props.modelValue)),
  set: (v) => emit('update:modelValue', v === EMPTY ? '' : v),
})

// Render the trigger label ourselves from modelValue + options rather than
// Reka's <SelectValue>: SelectValue mirrors the selected item from Reka's
// internal collection, which goes empty for one frame during the
// open→select→close transition — collapsing a width:auto trigger to the
// chevron and reflowing any sibling row (the facet-pill "blink"). A
// props-derived label never empties, so the trigger width stays stable.
const selectedItem = computed(() =>
  items.value.find((o) => !o.separator && o.value === props.modelValue) || null,
)
const isPlaceholder = computed(() => props.modelValue == null || selectedItem.value == null)
const displayLabel = computed(() => (selectedItem.value ? selectedItem.value.label : props.placeholder))
</script>

<template>
  <SelectRoot v-model="model" :disabled="disabled">
    <SelectTrigger
      v-bind="$attrs"
      :class="cn('input ds-select-trigger', block && 'is-block', bare && 'ds-select-trigger--bare', $props.class)"
    >
      <span class="ds-select-value" :data-placeholder="isPlaceholder ? '' : undefined">{{ displayLabel }}</span>
      <SelectIcon class="ds-select-chevron">
        <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <path d="M6 8l4 4 4-4" stroke="currentColor" stroke-width="1.5"
            stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </SelectIcon>
    </SelectTrigger>
    <SelectPortal>
      <SelectContent class="ds-select-content" position="popper" :side-offset="6">
        <SelectViewport class="ds-select-viewport">
          <template v-for="(opt, i) in items" :key="opt.separator ? `sep-${i}` : String(opt.value)">
            <SelectSeparator v-if="opt.separator" class="ds-select-sep" />
            <SelectItem
              v-else
              class="ds-select-item"
              :value="toReka(opt.value)"
              :disabled="opt.disabled"
            >
              <span class="ds-select-check">
                <SelectItemIndicator>
                  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                    <path d="M5 10l3.5 3.5L15 6" stroke="currentColor" stroke-width="1.75"
                      stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </SelectItemIndicator>
              </span>
              <SelectItemText>{{ opt.label }}</SelectItemText>
              <span v-if="opt.count != null" class="ds-select-count">{{ opt.count }}</span>
            </SelectItem>
          </template>
        </SelectViewport>
      </SelectContent>
    </SelectPortal>
  </SelectRoot>
</template>

<!-- NOT scoped: SelectContent is portalled to <body>, out of reach of Vue's
     scoped data-v attribute (mirrors DropdownMenu.vue). Classes are ds-* keyed. -->
<style>
.ds-select-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  /* Beat the unlayered `.input { width: 100% }` — see the .input-clip lesson. */
  width: auto !important;
  max-width: 100%;
  overflow: hidden;
  text-align: left;
  cursor: pointer;
}
.ds-select-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.ds-select-trigger.is-block { width: 100% !important; }
.ds-select-trigger:disabled { cursor: not-allowed; }
.ds-select-trigger[data-state="open"] {
  border-color: var(--color-ring);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-ring) 22%, transparent);
}
.ds-select-trigger[data-placeholder] { color: var(--color-fg-subtle); }

/* Bare: host chrome (a pill/segment) owns the border + focus state. */
.ds-select-trigger--bare {
  background: transparent !important;
  border: 0 !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  padding: 0.25rem 0.5rem 0.25rem 0.625rem !important;
  font-weight: 500;
  color: var(--color-fg);
}

.ds-select-chevron {
  display: inline-flex;
  flex: none;
  color: var(--color-fg-subtle);
  transition: transform 150ms;
}
.ds-select-chevron svg { width: 1rem; height: 1rem; }
.ds-select-trigger[data-state="open"] .ds-select-chevron { transform: rotate(180deg); }

.ds-select-content {
  z-index: var(--z-dropdown);
  min-width: max(11rem, var(--reka-select-trigger-width));
  max-height: min(20rem, var(--reka-select-content-available-height, 20rem));
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--ds-shadow-lg);
  padding: 0.25rem;
  overflow: hidden;
}
.ds-select-viewport { overflow-y: auto; max-height: inherit; }
.ds-select-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4375rem 0.5rem 0.4375rem 0.375rem;
  font-size: 0.8125rem;
  color: var(--color-fg);
  border-radius: var(--radius-md);
  cursor: pointer;
  outline: none;
  user-select: none;
}
.ds-select-item[data-highlighted] { background: var(--color-surface-2); }
.ds-select-item[data-state="checked"] { color: var(--color-primary); font-weight: 500; }
.ds-select-item[data-disabled] { opacity: 0.5; cursor: not-allowed; }
.ds-select-check {
  display: inline-flex;
  flex: none;
  width: 1rem;
  height: 1rem;
  color: var(--color-primary);
}
.ds-select-check svg { width: 1rem; height: 1rem; }
.ds-select-count {
  margin-left: auto;
  padding-left: 1rem;
  color: var(--color-fg-subtle);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
}
.ds-select-item[data-highlighted] .ds-select-count { color: var(--color-fg-muted); }
.ds-select-sep { height: 1px; background: var(--color-border-subtle); margin: 0.25rem 0.375rem; }
</style>
