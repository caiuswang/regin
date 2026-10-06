<script setup>
// Pixel MenuField: a key reading "FIELD · CURRENT ▼" that opens a listbox
// headed by what is being picked, with a ▶ cursor on the highlighted option.
// Built on the same Reka select as classic's ui/Select, so it is a combobox
// with a listbox of options either way: Enter / ↑↓ / Esc / outside click,
// typeahead and the value round-trip behave as the classic menu does.
import { computed } from 'vue'
import {
  SelectRoot, SelectTrigger, SelectPortal, SelectContent, SelectViewport,
  SelectItem, SelectItemText,
} from 'reka-ui'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: '' },
  options: { type: Array, default: () => [] },
  field: { type: String, default: '' },
  head: { type: String, default: '' },
  ariaLabel: { type: String, default: '' },
  testid: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

// Reka reserves "" as its clear sentinel, and an "Any …" option is a real "".
const EMPTY = ' pl-empty'
const toReka = v => (v === '' ? EMPTY : v)

const items = computed(() => props.options.map(o => ({
  value: o.value,
  label: o.short ?? o.label ?? String(o.value),
})))
const model = computed({
  get: () => (props.modelValue == null ? undefined : toReka(props.modelValue)),
  set: v => emit('update:modelValue', v === EMPTY ? '' : v),
})
const current = computed(() => items.value.find(o => o.value === props.modelValue)?.label ?? '')
</script>

<template>
  <SelectRoot v-model="model">
    <SelectTrigger
      data-pl-part="menu-key"
      :aria-label="ariaLabel || undefined"
      :data-testid="testid || undefined"
      :data-value="String(modelValue)"
    >
      <span v-if="field" data-pl-part="menu-field">{{ field }}</span>
      <span data-pl-part="menu-value">{{ current }}</span>
      <span data-pl-part="menu-caret" aria-hidden="true">▼</span>
    </SelectTrigger>
    <SelectPortal>
      <SelectContent
        position="popper"
        align="start"
        :side-offset="4"
        :style="{ zIndex: 'var(--z-popover)' }"
        data-pl-part="listbox"
        data-pl-menu=""
      >
        <div v-if="head" data-pl-part="listbox-head">{{ head }}</div>
        <SelectViewport>
          <SelectItem
            v-for="o in items"
            :key="String(o.value)"
            :value="toReka(o.value)"
            :data-value="String(o.value)"
            data-pl-part="listbox-item"
          >
            <SelectItemText>{{ o.label }}</SelectItemText>
          </SelectItem>
        </SelectViewport>
      </SelectContent>
    </SelectPortal>
  </SelectRoot>
</template>
