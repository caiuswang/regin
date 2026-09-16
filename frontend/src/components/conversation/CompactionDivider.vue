<script setup>
import { fmtClock, fmtTokens } from '../../utils/traceFormatters.js'

// The PreCompact / PostCompact boundary: a dashed rule across the feed with a
// pill naming the trigger, what it freed, and when — plus the summary the
// compaction was handed, collapsed to a line until asked for in full.
defineProps({
  span: { type: Object, required: true },
  // useConversationFolding: { isPromptBodyExpanded, togglePromptBodyExpanded }
  folding: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})
defineEmits(['select'])
</script>

<template>
  <div
    class="my-3 px-2 py-1 cursor-pointer group rounded-lg border border-transparent transition-colors hover:bg-amber-50/60 focus-visible:outline-2 focus-visible:outline-amber-400"
    :class="selected ? 'event-selected' : ''"
    tabindex="0"
    role="button"
    @click="$emit('select')"
    @keydown.enter.prevent="$emit('select')"
    @keydown.space.prevent="$emit('select')"
  >
    <div class="flex min-w-0 items-center gap-2 text-amber-700">
      <div class="flex-1 border-t border-dashed border-amber-300"></div>
      <!-- The pill wraps rather than staying on one line: with a trigger,
           a freed-tokens figure and a clock it runs ~407px, wider than a
           phone's content pane, and `whitespace-nowrap` turned that into
           a sideways scroll of the whole feed. -->
      <span class="inline-flex min-w-0 max-w-full flex-wrap items-center justify-center gap-1.5 text-[11px] font-mono uppercase tracking-[0.05em] px-2.5 py-[3px] rounded-[7px] bg-amber-50 border border-amber-200">
        <span>{{ span.name === 'compact.pre' ? '▼ context compacting' : '▲ context compacted' }}</span>
        <span v-if="span.attributes?.trigger" class="text-amber-500">·</span>
        <span v-if="span.attributes?.trigger" class="lowercase">{{ span.attributes.trigger }}</span>
        <span
          v-if="span.attributes?.reclaimed_tokens"
          class="text-emerald-700 normal-case"
          :title="'context tokens reclaimed: last turn before compaction minus first turn after (measured from turn_usage.context_used_tokens)'"
        >· freed ~{{ fmtTokens(span.attributes.reclaimed_tokens) }}</span>
        <span class="text-amber-400">·</span>
        <span class="text-amber-600 normal-case">{{ fmtClock(span.start_time) }}</span>
      </span>
      <div class="flex-1 border-t border-dashed border-amber-300"></div>
    </div>
    <div
      v-if="span.attributes?.custom_instructions"
      class="mt-1 text-xs text-amber-800/80 italic text-center px-4 break-words"
    >
      “{{ span.attributes.custom_instructions }}”
    </div>
    <div
      v-if="span.attributes?.summary"
      class="mt-1 text-[11px] text-slate-500 px-4"
    >
      <div v-if="!folding.isPromptBodyExpanded(span.span_id)" class="text-center break-words">
        <span class="text-slate-600">summary:</span>
        {{ span.attributes.summary.slice(0, 180) }}{{ span.attributes.summary.length > 180 ? '…' : '' }}
        <span v-if="span.attributes.summary_chars" class="text-slate-400">({{ span.attributes.summary_chars.toLocaleString() }} chars)</span>
        <button
          v-if="span.attributes.summary.length > 180"
          type="button"
          class="ml-1 text-amber-600 hover:text-amber-700 hover:underline cursor-pointer focus-visible:outline-2 focus-visible:outline-amber-400 rounded"
          @click.stop="folding.togglePromptBodyExpanded(span.span_id)"
        >show full ▾</button>
      </div>
      <div v-else>
        <div class="flex items-center justify-between mb-1">
          <span class="text-slate-600 font-medium">summary <span v-if="span.attributes.summary_chars" class="text-slate-400 font-normal">({{ span.attributes.summary_chars.toLocaleString() }} chars)</span></span>
          <button
            type="button"
            class="text-amber-600 hover:text-amber-700 hover:underline cursor-pointer focus-visible:outline-2 focus-visible:outline-amber-400 rounded"
            @click.stop="folding.togglePromptBodyExpanded(span.span_id)"
          >collapse ▴</button>
        </div>
        <pre class="whitespace-pre-wrap break-words text-slate-600 bg-amber-50/30 border border-amber-100 rounded px-3 py-2 max-h-[60vh] overflow-y-auto font-sans">{{ span.attributes.summary }}</pre>
      </div>
    </div>
  </div>
</template>
