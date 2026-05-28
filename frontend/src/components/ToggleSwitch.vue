<script setup>
const props = defineProps({
  modelValue: { type: Boolean, required: true },
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  onLabel: { type: String, default: 'On' },
  offLabel: { type: String, default: 'Off' },
})
const emit = defineEmits(['update:modelValue', 'change'])
function toggle() {
  if (props.loading || props.disabled) return
  const next = !props.modelValue
  emit('update:modelValue', next)
  emit('change', next)
}
</script>

<template>
  <button
    type="button"
    role="switch"
    :aria-checked="modelValue"
    :aria-busy="loading"
    :disabled="disabled || loading"
    @click="toggle"
    class="inline-flex items-center gap-2 group focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded-full"
  >
    <span
      class="relative inline-block h-5 w-9 rounded-full transition-colors"
      :class="[
        modelValue ? 'bg-blue-600' : 'bg-gray-300',
        (disabled || loading) ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer group-hover:brightness-95',
      ]"
    >
      <span
        class="absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all"
        :class="modelValue ? 'left-[18px]' : 'left-0.5'"
      />
    </span>
    <span
      class="text-xs font-medium w-14 text-left"
      :class="modelValue ? 'text-blue-700' : 'text-gray-500'"
    >
      <template v-if="loading">…</template>
      <template v-else>{{ modelValue ? onLabel : offLabel }}</template>
    </span>
  </button>
</template>
