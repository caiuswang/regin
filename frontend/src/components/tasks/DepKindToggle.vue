<script setup>
import Button from '../ui/Button.vue'

// The two things a dependency edge can mean. `requires` is the strict wait;
// `after` is an ordering, so the task runs once the one above it has stopped
// — succeeded or not.
const KINDS = [
  { value: 'requires', label: 'needs it' },
  { value: 'after', label: 'just after' },
]

defineProps({
  modelValue: { type: String, default: 'requires' },
  label: { type: String, required: true },
})
const emit = defineEmits(['update:modelValue'])
</script>

<template>
  <div class="dkt" role="radiogroup" :aria-label="label">
    <Button
      v-for="k in KINDS"
      :key="k.value"
      variant="ghost"
      size="sm"
      role="radio"
      class="dkt-opt"
      data-pl-part="key"
      :class="{ 'is-on': modelValue === k.value }"
      :aria-checked="modelValue === k.value"
      :data-kind="k.value"
      @click="emit('update:modelValue', k.value)"
    >
      {{ k.label }}
    </Button>
  </div>
</template>

<style scoped>
.dkt {
    display: inline-flex;
    flex-shrink: 0;
    padding: 0.125rem;
    background: var(--color-surface-3);
    border-radius: var(--radius-md);
}
.dkt .dkt-opt.is-on {
    color: var(--color-fg);
    background: var(--color-surface);
    box-shadow: 0 1px 2px color-mix(in srgb, var(--color-fg) 10%, transparent);
}
</style>
