<script setup>
// Per-surface agent binding control. A goal-prompt (skeleton) can be *bound*
// to one configured external agent; the empty option clears the binding so the
// dispatch falls back to that surface's default agent. Purely presentational —
// the parent owns the PATCH.
import { computed } from 'vue'
import Select from './ui/Select.vue'

const props = defineProps({
  // The bound agent id, or null/'' when unbound (→ default agent).
  modelValue: { type: String, default: null },
  // Configured agents as [{ id, command }].
  agents: { type: Array, default: () => [] },
  // The default agent id, shown on the fallback option for context.
  defaultAgent: { type: String, default: null },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const defaultLabel = computed(() =>
  props.defaultAgent ? `Default (${props.defaultAgent})` : 'Default',
)
const options = computed(() => [
  { value: '', label: defaultLabel.value },
  ...props.agents.map((a) => ({ value: a.id, label: a.id })),
])
const value = computed(() => props.modelValue || '')

function onChange(next) {
  emit('update:modelValue', next || null)
}
</script>

<template>
  <Select
    :model-value="value"
    :options="options"
    :disabled="disabled || !agents.length"
    aria-label="Bound agent"
    @update:model-value="onChange"
  />
</template>
