<script setup>
// Single-choice radio group on Reka: a custom-drawn control (ring + filled
// dot in the brand accent) instead of the browser's small gray native radio,
// with roving arrow-key focus and proper radiogroup/radio semantics for free.
// Drop-in API (modelValue / options / name / disabled / inline); the value
// round-trips as its original type. Pass `options` as strings or
// { value, label, disabled }.
import { computed } from 'vue'
import { RadioGroupRoot, RadioGroupItem, RadioGroupIndicator } from 'reka-ui'

const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: null },
  options: { type: Array, default: () => [] },
  name: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  inline: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const model = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})
const normalized = computed(() =>
  props.options.map((o) =>
    o !== null && typeof o === 'object'
      ? { value: o.value, label: o.label ?? String(o.value), disabled: !!o.disabled }
      : { value: o, label: String(o), disabled: false },
  ),
)
</script>

<template>
  <RadioGroupRoot
    v-model="model"
    :name="name || undefined"
    :disabled="disabled"
    :orientation="inline ? 'horizontal' : 'vertical'"
    class="ds-radio-group"
    :class="{ 'ds-radio-inline': inline }"
  >
    <label
      v-for="opt in normalized"
      :key="String(opt.value)"
      class="ds-radio"
      :class="{ 'ds-radio-disabled': disabled || opt.disabled }"
    >
      <RadioGroupItem class="ds-radio-control" :value="opt.value" :disabled="opt.disabled">
        <RadioGroupIndicator class="ds-radio-indicator" />
      </RadioGroupItem>
      <span>{{ opt.label }}</span>
    </label>
  </RadioGroupRoot>
</template>

<style scoped>
.ds-radio-group { display: flex; flex-direction: column; gap: 0.5rem; }
.ds-radio-inline { flex-direction: row; flex-wrap: wrap; gap: 1rem; }
.ds-radio {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  font-size: 0.8125rem;
  color: var(--color-fg);
}
.ds-radio-disabled { cursor: not-allowed; opacity: 0.6; }
.ds-radio-control {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1rem;
  height: 1rem;
  padding: 0;
  border: 1.5px solid var(--color-border-strong);
  border-radius: 50%;
  background: var(--color-surface);
  cursor: inherit;
  outline: none;
  transition: border-color 150ms, box-shadow 150ms;
}
.ds-radio:hover .ds-radio-control:not([data-disabled]) { border-color: var(--color-fg-muted); }
.ds-radio-control[data-state="checked"] { border-color: var(--color-primary); }
.ds-radio-control:focus-visible {
  border-color: var(--color-ring);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-ring) 22%, transparent);
}
.ds-radio-control[data-disabled] { cursor: not-allowed; }
.ds-radio-indicator {
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: var(--color-primary);
}
</style>
