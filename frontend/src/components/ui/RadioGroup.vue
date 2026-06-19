<script setup>
// Single-choice radio group rendered from an options list, so callers stop
// hand-writing N <input type="radio"> with divergent styling/labels.
// Pass `options` as strings or { value, label, disabled }.
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: null },
  options: { type: Array, default: () => [] },
  name: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  inline: { type: Boolean, default: false },
})
defineEmits(['update:modelValue'])

const groupName = computed(() => props.name || `ds-radio-${Math.round(performance.now())}`)
const normalized = computed(() =>
  props.options.map((o) =>
    o !== null && typeof o === 'object'
      ? { value: o.value, label: o.label ?? String(o.value), disabled: !!o.disabled }
      : { value: o, label: String(o), disabled: false },
  ),
)
</script>

<template>
  <div class="ds-radio-group" :class="{ 'ds-radio-inline': inline }" role="radiogroup">
    <label
      v-for="opt in normalized"
      :key="String(opt.value)"
      class="ds-radio"
      :class="{ 'ds-radio-disabled': disabled || opt.disabled }"
    >
      <input
        type="radio"
        class="ds-radio-input"
        :name="groupName"
        :value="opt.value"
        :checked="modelValue === opt.value"
        :disabled="disabled || opt.disabled"
        @change="$emit('update:modelValue', opt.value)"
      />
      <span>{{ opt.label }}</span>
    </label>
  </div>
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
.ds-radio-input {
  width: 1rem;
  height: 1rem;
  accent-color: var(--color-primary);
  cursor: inherit;
}
.ds-radio-input:focus-visible {
  outline: 2px solid var(--color-ring);
  outline-offset: 2px;
}
</style>
