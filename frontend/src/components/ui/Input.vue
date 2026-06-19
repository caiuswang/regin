<script setup>
// Text/search/number/email/etc. input. One styling source via the
// token-backed `.input` class. v-model + a `error` flag for invalid state.
import { ref } from 'vue'
import { cn } from '../../utils/cn'

defineProps({
  modelValue: { type: [String, Number], default: '' },
  type: { type: String, default: 'text' },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  error: { type: Boolean, default: false },
  class: { type: null, default: '' },
})
defineEmits(['update:modelValue'])

// Expose focus() so a template ref on <Input> behaves like one on a
// native <input> (e.g. autofocusing a login field).
const el = ref(null)
defineExpose({ focus: () => el.value?.focus() })
</script>

<template>
  <input
    ref="el"
    :type="type"
    :value="modelValue"
    :placeholder="placeholder"
    :disabled="disabled"
    :aria-invalid="error || undefined"
    :class="cn('input', error && 'ds-input-error', $props.class)"
    @input="$emit('update:modelValue', $event.target.value)"
  />
</template>
