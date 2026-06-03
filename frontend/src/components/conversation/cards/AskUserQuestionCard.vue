<script setup>
import {
  fmtClock, fmtDuration, dotColor,
  askOptLabel, askOptDescription, askIsChosen, askFreeText, askNote,
} from '../../../utils/traceFormatters.js'

// AskUserQuestion: full Q&A cards inline. Both approved and denied calls land
// here — denied ones are synthesized by turn_trace from the transcript when
// is_error=true. `attributes.denied` flips the styling: amber border, no chosen
// option, and the user's actual response (`denial_reason`) below the options.
defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
})
defineEmits(['activate'])
</script>

<template>
  <div
    tabindex="0"
    class="ml-3 -mx-1 my-1 cursor-pointer rounded focus-visible:outline-2"
    :class="[
      span.attributes.denied ? 'focus-visible:outline-amber-500' : 'focus-visible:outline-blue-500',
      selectedSpan && selectedSpan.span_id === span.span_id
        ? (span.attributes.denied ? 'ring-2 ring-amber-200' : 'ring-2 ring-blue-200')
        : '',
    ]"
    @click="$emit('activate', span)"
  >
    <div class="flex items-center gap-2 text-[11px] font-mono text-slate-400 mb-1 px-1">
      <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0"
            :class="span.attributes.denied ? 'bg-amber-400' : dotColor(span.name)"></span>
      <span>{{ fmtClock(span.start_time) }}</span>
      <span class="font-sans uppercase tracking-wider text-[10px] text-slate-500 font-semibold">Ask user</span>
      <span
        v-if="span.attributes.denied"
        class="font-sans uppercase tracking-wider text-[10px] bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded"
      >{{ span.attributes.deny_kind === 'chat' ? 'chat instead' : 'denied' }}</span>
      <span v-if="span.duration_ms" class="ml-auto">{{ fmtDuration(span.duration_ms) }}</span>
    </div>
    <div class="space-y-2">
      <div
        v-for="(q, qi) in span.attributes.questions"
        :key="qi"
        class="border rounded-md overflow-hidden bg-white"
        :class="span.attributes.denied ? 'border-amber-200 opacity-90' : 'border-slate-200'"
      >
        <div
          class="px-3 py-1.5 border-b"
          :class="span.attributes.denied ? 'bg-amber-50 border-amber-200' : 'bg-slate-50 border-slate-200'"
        >
          <div
            v-if="q.header"
            class="text-[10px] font-semibold uppercase tracking-wider mb-0.5"
            :class="span.attributes.denied ? 'text-amber-700' : 'text-slate-500'"
          >{{ q.header }}{{ q.multiSelect ? ' · multi-select' : '' }}</div>
          <div class="text-[13px] font-medium text-slate-800">{{ q.question }}</div>
        </div>
        <ul class="divide-y divide-slate-100">
          <li
            v-for="(opt, oi) in (q.options || [])"
            :key="oi"
            class="flex items-start gap-2 px-3 py-1.5 text-[12.5px]"
            :class="askIsChosen(span, q, opt) ? 'bg-green-50' : ''"
          >
            <span
              class="shrink-0 mt-0.5 w-4 text-center font-mono text-xs"
              :class="askIsChosen(span, q, opt) ? 'text-green-600' : 'text-slate-300'"
            >{{ askIsChosen(span, q, opt) ? '✓' : '○' }}</span>
            <span class="min-w-0 flex-1">
              <span
                class="block"
                :class="askIsChosen(span, q, opt) ? 'text-slate-900 font-medium' : 'text-slate-800 font-medium'"
              >{{ askOptLabel(opt) }}</span>
              <span
                v-if="askOptDescription(opt)"
                class="block text-slate-500 mt-0.5"
              >{{ askOptDescription(opt) }}</span>
              <details
                v-if="opt && opt.preview"
                class="mt-1"
                @click.stop
              >
                <summary class="cursor-pointer text-[10px] text-slate-500 hover:text-slate-700 select-none">Preview</summary>
                <pre class="mt-1 text-[11px] text-slate-700 bg-slate-50 border border-slate-200 rounded p-2 whitespace-pre-wrap break-words max-h-64 overflow-y-auto font-mono">{{ opt.preview }}</pre>
              </details>
            </span>
          </li>
          <li
            v-if="askFreeText(span, q)"
            class="flex items-start gap-2 px-3 py-1 text-[12.5px] bg-amber-50"
          >
            <span class="shrink-0 mt-0.5 w-4 text-center font-mono text-xs text-amber-600">✎</span>
            <span class="text-slate-900">{{ askFreeText(span, q) }}</span>
          </li>
        </ul>
        <div
          v-if="askNote(span, q)"
          class="px-3 py-1 bg-slate-50 border-t border-slate-100 text-[11px] text-slate-600 italic"
        >
          Note: {{ askNote(span, q) }}
        </div>
      </div>
      <div
        v-if="span.attributes.denied && span.attributes.denial_reason"
        class="border border-amber-200 bg-amber-50 rounded-md px-3 py-2 text-[12px] text-slate-700 whitespace-pre-wrap"
      >
        <div
          class="text-[10px] font-semibold uppercase tracking-wider text-amber-700 mb-1"
          title="Templated text the agent harness (Claude Code) injects when the user denies a tool call — not user prose."
        >Denied (agent injected prompt)</div>
        {{ span.attributes.denial_reason }}
      </div>
    </div>
  </div>
</template>
