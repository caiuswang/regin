<script setup>
import { computed } from 'vue'
import { fmtDuration, rowParts } from '../../../utils/traceFormatters.js'

// ToolSearch: collapsed row matches the generic inline look; expanded panel
// surfaces query, loaded_tools, max_results and the search-universe size.
const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  // useConversationFolding: { toolSearchExpanded, toggleToolSearchExpanded }
  folding: { type: Object, required: true },
})
defineEmits(['activate'])

const hasBody = computed(() => {
  const a = props.span?.attributes || {}
  return Boolean(a.query || a.max_results != null || a.loaded_tools?.length || a.selected_tools?.length)
})
</script>

<template>
  <div class="group">
    <div
      tabindex="0"
      class="flex items-baseline gap-[9px] cursor-pointer rounded-lg border border-transparent px-2.5 py-[5px] hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="selectedSpan && selectedSpan.span_id === span.span_id ? 'event-selected' : ''"
      @click="$emit('activate', span); folding.toggleToolSearchExpanded(span.span_id)"
    >
      <span class="text-[12.5px] font-semibold text-slate-700 shrink-0">{{ rowParts(span).title }}</span>
      <span class="font-mono text-[12.5px] text-slate-500 flex-1 min-w-0 truncate">{{ rowParts(span).subject }}</span>
      <span v-if="span.duration_ms" class="font-mono text-[10.5px] text-slate-400 shrink-0">{{ fmtDuration(span.duration_ms) }}</span>
      <!-- Pre-feature spans captured none of the body fields; their caret
           opened an empty panel. -->
      <span
        v-if="hasBody"
        class="text-[10.5px] text-slate-400 shrink-0 select-none w-3 text-center"
      >{{ folding.toolSearchExpanded(span.span_id) ? '▾' : '▸' }}</span>
    </div>
    <div
      v-if="hasBody && folding.toolSearchExpanded(span.span_id)"
      class="ml-2.5 mt-1 rounded-lg bg-slate-50 border border-slate-200 px-3 py-2 text-[12px] font-mono text-slate-700 space-y-1"
    >
      <div v-if="span.attributes?.query" class="flex gap-2">
        <span class="text-slate-400 shrink-0 w-28">query</span>
        <span class="break-all">{{ span.attributes.query }}</span>
      </div>
      <div v-if="span.attributes?.max_results != null" class="flex gap-2">
        <span class="text-slate-400 shrink-0 w-28">max_results</span>
        <span>{{ span.attributes.max_results }}</span>
      </div>
      <div v-if="span.attributes?.loaded_tools?.length" class="flex gap-2">
        <span class="text-slate-400 shrink-0 w-28">loaded ({{ span.attributes.loaded_tools.length }})</span>
        <span class="break-all">{{ span.attributes.loaded_tools.join(', ') }}</span>
      </div>
      <div
        v-else-if="span.attributes?.selected_tools?.length"
        class="flex gap-2"
        :title="'No tool_response.matches captured — falling back to the parsed select: list. Pre-feature spans only.'"
      >
        <span class="text-slate-400 shrink-0 w-28">selected ({{ span.attributes.selected_tools.length }})</span>
        <span class="break-all">{{ span.attributes.selected_tools.join(', ') }}</span>
      </div>
      <div v-if="span.attributes?.total_deferred_tools != null" class="flex gap-2">
        <span class="text-slate-400 shrink-0 w-28">deferred pool</span>
        <span>{{ span.attributes.total_deferred_tools }}</span>
      </div>
    </div>
  </div>
</template>
