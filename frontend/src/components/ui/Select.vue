<script setup>
// Unified single-select. Token-styled native <select> (accessible by
// default, robust on mobile) with a consistent chevron. Replaces the 3
// divergent select-styling patterns the audit found (.input / .topics-input /
// bare inline). Pass `options` as strings or { value, label, disabled }.
import { computed } from 'vue'
import { cn } from '../../utils/cn'

// Root is the wrapper <div>, so forward fallthrough attrs (aria-label, id,
// name, …) to the inner <select> instead of the wrapper.
defineOptions({ inheritAttrs: false })

const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: '' },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  // By default the control sizes to its widest option (capped at the
  // container) so it never stretches ugly-wide in a toolbar/flex row. Set
  // `block` for form fields that should fill their column / fixed-width slot.
  block: { type: Boolean, default: false },
  class: { type: null, default: '' },
})
defineEmits(['update:modelValue'])

const normalized = computed(() =>
  props.options.map((o) =>
    o !== null && typeof o === 'object'
      ? { value: o.value, label: o.label ?? String(o.value), disabled: !!o.disabled }
      : { value: o, label: String(o), disabled: false },
  ),
)
</script>

<template>
  <div :class="cn('ds-select-wrap', block && 'is-block')">
    <select
      v-bind="$attrs"
      :value="modelValue"
      :disabled="disabled"
      :class="cn('input ds-select', $props.class)"
      @change="$emit('update:modelValue', $event.target.value)"
    >
      <option v-if="placeholder" value="" disabled>{{ placeholder }}</option>
      <option
        v-for="opt in normalized"
        :key="String(opt.value)"
        :value="opt.value"
        :disabled="opt.disabled"
      >
        {{ opt.label }}
      </option>
    </select>
    <svg class="ds-select-chevron" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M6 8l4 4 4-4" stroke="currentColor" stroke-width="1.5"
        stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </div>
</template>

<style scoped>
/* Default: shrink to the widest option, capped at the container — keeps a
   bare <Select> from stretching ugly-wide in toolbars and flex rows. */
.ds-select-wrap {
  position: relative;
  display: inline-flex;
  width: fit-content;
  max-width: 100%;
}
/* Opt-in full width for form fields that should fill their column. */
.ds-select-wrap.is-block { width: 100%; }
.ds-select {
  appearance: none;
  -webkit-appearance: none;
  width: 100%;
  padding-right: 2rem;
  cursor: pointer;
  text-overflow: ellipsis;
}
.ds-select-chevron {
  position: absolute;
  right: 0.625rem;
  top: 50%;
  transform: translateY(-50%);
  width: 1rem;
  height: 1rem;
  pointer-events: none;
  color: var(--color-fg-subtle);
}
</style>
