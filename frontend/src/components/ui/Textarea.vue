<script setup>
// Multi-line text. Same token-backed `.input` look as Input; vertical resize.
import { cn } from '../../utils/cn'

defineProps({
  modelValue: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  rows: { type: [String, Number], default: 4 },
  disabled: { type: Boolean, default: false },
  error: { type: Boolean, default: false },
  class: { type: null, default: '' },
})
defineEmits(['update:modelValue'])
</script>

<template>
  <textarea
    :value="modelValue"
    :placeholder="placeholder"
    :rows="rows"
    :disabled="disabled"
    :aria-invalid="error || undefined"
    :class="cn('input resize-y', error && 'ds-input-error', $props.class)"
    @input="$emit('update:modelValue', $event.target.value)"
  />
</template>
