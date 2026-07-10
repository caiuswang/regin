<script setup>
// The Messages tab of the session trace: the session's send_to_user feed as a
// vertical timeline (goal header + one node per message). Extracted from
// SessionTraceView so the host template stays within its complexity budget;
// purely presentational (the parent owns fetching + the jump-to-span scroll,
// which anchors each <li> by `msg-<span_id>`).
import MarkdownContent from './MarkdownContent.vue'
import CopyButton from './conversation/cards/CopyButton.vue'

defineProps({
  // send_to_user rows, or null before the first fetch (distinguishes
  // "not fetched" from "fetched, empty").
  messages: { type: Array, default: null },
  sessionGoal: { type: String, default: null },
  // span_id of the message to briefly highlight after a jump.
  highlightedSpan: { type: String, default: null },
})

// Pill colour for a non-progress message type (matches InboxMessageCard).
const MESSAGE_TYPE_CLASS = {
  result: 'bg-emerald-100 text-emerald-700',
  summary: 'bg-indigo-100 text-indigo-700',
  warning: 'bg-amber-100 text-amber-800',
  blocker: 'bg-red-100 text-red-700',
  note: 'bg-slate-100 text-slate-600',
}
function messageTypeClass(t) {
  return MESSAGE_TYPE_CLASS[t] || 'bg-slate-100 text-slate-600'
}
</script>

<template>
  <div class="px-4 py-8 lg:px-8">
    <div v-if="messages == null" class="text-slate-500 text-sm py-16 text-center">
      Loading messages…
    </div>
    <div v-else-if="!messages.length" class="text-slate-500 text-sm py-16 text-center">
      No send_to_user messages in this session.
    </div>
    <div v-else class="w-full">
      <div
        v-if="sessionGoal"
        class="mb-8 rounded-lg border border-slate-200 bg-slate-50 px-5 py-4"
      >
        <div class="flex items-center gap-1.5 mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
          <svg class="w-3.5 h-3.5 text-blue-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" />
          </svg>
          Session goal
        </div>
        <div class="text-sm text-slate-900 leading-relaxed whitespace-pre-wrap">{{ sessionGoal }}</div>
      </div>
      <ol class="relative ml-2 border-l-2 border-slate-100 space-y-7 pb-2">
        <li
          v-for="(m, i) in messages"
          :key="m.id ?? m.span_id"
          :id="m.span_id ? `msg-${m.span_id}` : undefined"
          class="group relative pl-7 scroll-mt-28"
        >
          <span
            class="absolute -left-[9px] top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-white border-2 transition-colors duration-200"
            :class="i === messages.length - 1 ? 'border-blue-500' : 'border-slate-300'"
          >
            <span
              class="h-1.5 w-1.5 rounded-full"
              :class="i === messages.length - 1 ? 'bg-blue-500' : 'bg-slate-300'"
            ></span>
          </span>
          <div class="flex items-baseline gap-2 mb-1.5">
            <span class="text-xs font-semibold text-slate-700">#{{ i + 1 }}</span>
            <span
              v-if="m.msg_type && m.msg_type !== 'progress'"
              class="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
              :class="messageTypeClass(m.msg_type)"
            >{{ m.msg_type }}</span>
            <span v-if="m.title" class="text-xs font-semibold text-slate-800">{{ m.title }}</span>
            <span class="text-[11px] font-mono text-slate-500">
              {{ new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }}
            </span>
            <CopyButton
              v-if="m.body"
              :text="m.body"
              tint="text-slate-400 hover:bg-slate-200/60 hover:text-slate-700"
            />
          </div>
          <div
            class="rounded-lg border bg-white px-4 py-3 shadow-sm transition-colors duration-200"
            :class="highlightedSpan === m.span_id
              ? 'border-blue-400 ring-2 ring-blue-300'
              : 'border-slate-200 hover:border-slate-300'"
          >
            <MarkdownContent :markdown="m.body" />
          </div>
        </li>
      </ol>
    </div>
  </div>
</template>
