<script setup>
import MarkdownContent from '../../MarkdownContent.vue'
import CopyButton from './CopyButton.vue'

defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
</script>

<template>
  <!-- Server-side tool result card (e.g. advisor) — renders the full
       response_text as markdown, since the call's value is the textual
       reply, not a side effect. -->
  <div
    class="group rounded-lg border bg-violet-50 border-violet-200 px-3 py-2.5 cursor-pointer hover:border-violet-300 transition-colors"
    :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'event-selected' : ''"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 mb-1.5">
      <span class="font-semibold uppercase tracking-wider text-[10px] text-violet-700">{{ span.attributes?.tool_name || 'tool' }}</span>
      <span v-if="span.attributes?.response_truncated" class="text-amber-700 bg-amber-50 border border-amber-200 px-1 rounded text-[10px]">truncated</span>
      <CopyButton
        :text="span.attributes.response_text"
        tint="text-violet-600 hover:bg-violet-200/60"
      />
      <span
        v-if="span.attributes?.advisor_model"
        class="ml-auto font-mono text-[10.5px] text-slate-400"
      >{{ span.attributes.advisor_model }}</span>
    </div>
    <div class="text-[13.5px] text-slate-800">
      <MarkdownContent :markdown="span.attributes.response_text" />
    </div>
  </div>
</template>
