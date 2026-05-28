<script setup>
const props = defineProps({
  depth: { type: Number, default: 0 },
  leaf: { type: Boolean, default: false },
  expanded: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle'])
</script>

<template>
  <span class="inline-flex items-center shrink-0">
    <span
      v-for="i in Math.max(0, depth)"
      :key="`indent-${i}`"
      class="inline-block w-4 h-4 border-l border-gray-200"
      aria-hidden="true"
    ></span>
    <button
      v-if="!leaf"
      type="button"
      class="inline-flex items-center justify-center w-4 h-4 text-gray-500 hover:text-gray-700"
      :title="expanded ? 'Collapse' : 'Expand'"
      @click.stop="emit('toggle')"
    >
      {{ expanded ? '▾' : '▸' }}
    </button>
    <span v-else class="inline-block w-4 h-4" aria-hidden="true"></span>
  </span>
</template>

