<script setup>
// Boolean checkbox with a consistent accent color + focus ring and an
// optional inline label (clicking the label toggles it). Replaces ad-hoc
// raw <input type="checkbox"> styling.
defineProps({
  modelValue: { type: Boolean, default: false },
  label: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})
defineEmits(['update:modelValue'])
</script>

<template>
  <label class="ds-check" :class="{ 'ds-check-disabled': disabled }">
    <input
      type="checkbox"
      class="ds-check-box"
      :checked="modelValue"
      :disabled="disabled"
      @change="$emit('update:modelValue', $event.target.checked)"
    />
    <span v-if="label || $slots.default" class="ds-check-label">
      <slot>{{ label }}</slot>
    </span>
  </label>
</template>

<style scoped>
.ds-check {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  font-size: 0.8125rem;
  color: var(--color-fg);
}
.ds-check-disabled { cursor: not-allowed; opacity: 0.6; }
.ds-check-box {
  width: 1rem;
  height: 1rem;
  accent-color: var(--color-primary);
  cursor: inherit;
}
.ds-check-box:focus-visible {
  outline: 2px solid var(--color-ring);
  outline-offset: 2px;
}
</style>
