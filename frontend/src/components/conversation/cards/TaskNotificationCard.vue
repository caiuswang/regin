<script setup>
import { computed, ref } from 'vue'
import { fmtDuration, fmtTokens } from '../../../utils/traceFormatters.js'
import Button from '../../ui/Button.vue'

const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])

const PREVIEW_CHARS = 320

const attrs = computed(() => props.span.attributes || {})
const result = computed(() => attrs.value.result || '')
const expanded = ref(false)

// The finished agent's own cost of doing the work — the envelope carries all
// three and the card showed none of them.
const vitals = computed(() => {
  const a = attrs.value
  return [
    a.agent_type,
    fmtDuration(a.duration_ms),
    a.subagent_tokens ? `${fmtTokens(a.subagent_tokens)} tok` : '',
    a.tool_uses ? `${a.tool_uses} tool call${a.tool_uses === 1 ? '' : 's'}` : '',
  ].filter(Boolean)
})
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
        v-if="attrs.status"
        class="ml-auto px-1 rounded text-[10px] border font-mono"
        :class="attrs.status === 'completed'
          ? 'bg-green-50 text-green-700 border-green-200'
          : 'bg-red-50 text-red-700 border-red-200'"
      >{{ attrs.status }}</span>
    </div>
    <div
      v-if="attrs.summary"
      class="text-[13.5px] break-words text-amber-900 leading-relaxed"
    >{{ attrs.summary }}</div>
    <div
      v-if="vitals.length"
      class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] font-mono text-amber-700/80"
    >
      <template v-for="(bit, i) in vitals" :key="bit">
        <span v-if="i" aria-hidden="true">·</span><span>{{ bit }}</span>
      </template>
    </div>
    <div v-if="result" class="mt-1.5 border-t border-amber-200/70 pt-1.5">
      <Button
        v-if="result.length > PREVIEW_CHARS"
        variant="link"
        size="sm"
        class="min-w-0 max-w-full text-[11px] font-medium text-amber-700 hover:text-amber-900"
        @click.stop="expanded = !expanded"
      ><span class="truncate">{{ expanded ? 'Collapse report' : `Show report · ${result.length} chars` }}</span></Button>
      <div
        class="text-[12.5px] text-slate-700 whitespace-pre-wrap break-words leading-relaxed font-mono"
        :class="expanded ? 'max-h-[32rem] overflow-y-auto' : ''"
      >{{ expanded ? result : result.slice(0, PREVIEW_CHARS) }}</div>
    </div>
    <div
      v-if="attrs.task_id"
      class="text-[10px] font-mono text-amber-600/70 mt-0.5"
    >task {{ attrs.task_id }}</div>
  </div>
</template>
