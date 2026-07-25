<script setup>
defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
</script>

<template>
  <!-- Background-task notification card (amber tint) -->
  <div
    class="rounded-lg border bg-amber-50 border-amber-200 px-3 py-2.5 cursor-pointer hover:border-amber-300 transition-colors"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'event-selected' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 mb-1">
      <span class="font-semibold uppercase tracking-wider text-[10px] text-amber-700">Background task</span>
      <span
        v-if="span.attributes?.status"
        class="ml-auto px-1 rounded text-[10px] border font-mono"
        :class="span.attributes.status === 'failed'
          ? 'bg-red-50 text-red-700 border-red-200'
          : 'bg-green-50 text-green-700 border-green-200'"
      >{{ span.attributes.status }}</span>
    </div>
    <div
      v-if="span.attributes?.summary"
      class="text-[13.5px] break-words text-amber-900 leading-relaxed"
    >{{ span.attributes.summary }}</div>
    <div
      v-if="span.attributes?.task_id"
      class="text-[10px] font-mono text-amber-600/70 mt-0.5"
    >task {{ span.attributes.task_id }}</div>
  </div>
</template>
