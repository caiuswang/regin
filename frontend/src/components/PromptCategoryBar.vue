<script setup>
import { ALL } from '../composables/usePromptCategory'

defineProps({
  chips: { type: Array, required: true },
  total: { type: Number, required: true },
  modelValue: { type: String, default: ALL },
})
const emit = defineEmits(['update:modelValue'])
</script>

<template>
  <div class="filter-bar prompt-category-bar" role="group" aria-label="Filter by category">
    <button
      type="button"
      class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="{ active: modelValue === ALL }"
      :aria-pressed="modelValue === ALL"
      data-category="all"
      @click="emit('update:modelValue', ALL)"
    >All ({{ total }})</button>
    <button
      v-for="c in chips"
      :key="c.area"
      type="button"
      class="filter-chip focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="{ active: modelValue === c.area }"
      :aria-pressed="modelValue === c.area"
      :data-category="c.area"
      @click="emit('update:modelValue', c.area)"
    >
      <span class="category-dot" :class="`badge-${c.color}`" aria-hidden="true" />
      {{ c.label }} ({{ c.count }})
    </button>
  </div>
</template>

<style scoped>
.prompt-category-bar { margin-bottom: 0.75rem; }
.category-dot {
    display: inline-block;
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 9999px;
    margin-right: 0.4rem;
    flex: none;
}
</style>
