<script setup>
// Classic's ChoiceField: a pick among a few named options, as the select it
// always was. An option's hint is folded into its label ("tmux — survives a
// server restart"), which is how these menus read before themes.
import { computed } from 'vue'
import Select from '../ui/Select.vue'

const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: '' },
  // [{ value, label, hint?, glyph? }] — glyph is a theme's, never drawn here.
  options: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue'])

const items = computed(() => props.options.map(o => ({
  value: o.value,
  label: o.hint ? `${o.label} — ${o.hint}` : o.label,
})))
</script>

<template>
  <Select
    :model-value="modelValue"
    block
    :options="items"
    @update:model-value="v => emit('update:modelValue', v)"
  />
</template>
