<script setup>
// Classic's MenuField: a single pick from a short menu, drawn as the ui/Select
// it always was. `field` and `head` are a theme's words for the key and the
// menu's first line; classic shows neither, and an option's `short` label is
// likewise a theme's. `data-value` names the pick in every theme's markup.
import Select from '../ui/Select.vue'

defineOptions({ inheritAttrs: false })
defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: '' },
  // [{ value, label, short? }]
  options: { type: Array, default: () => [] },
  field: { type: String, default: '' },
  head: { type: String, default: '' },
  ariaLabel: { type: String, default: '' },
  testid: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])
</script>

<template>
  <Select
    v-bind="$attrs"
    :model-value="modelValue"
    :options="options"
    :aria-label="ariaLabel || undefined"
    :data-testid="testid || undefined"
    :data-value="String(modelValue)"
    @update:model-value="v => emit('update:modelValue', v)"
  />
</template>
