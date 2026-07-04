<script setup>
import { ref, computed } from 'vue'
import api from '../../api'
import { fmtClock } from '../../utils/traceFormatters.js'
import PromptBody from '../PromptBody.vue'
import ConversationSpanCard from './ConversationSpanCard.vue'

// `/rewind` marker: a rose dashed divider at the fork point. The discarded
// turns live behind it (hidden by default); here we surface the abandoned-
// prompt / rolled-back-file counts, the discarded branch itself (expandable —
// the abandoned prompt + its work, lazily fetched by the parent on toggle),
// and on demand a before/after of each file the rewind reverted.
const props = defineProps({
  span: { type: Object, required: true },
  traceId: { type: String, default: '' },
  // The marker's discarded subtree, as renderable {span, inAgent, agentSep}
  // rows. Empty until the parent lazily loads it on the first expand.
  descendants: { type: Array, default: () => [] },
  // Bundle the discarded span cards need: { selectedSpan, folding, agentMerge,
  // workflowRunsById }. Passed whole so the parent's template stays lean.
  ctx: { type: Object, default: () => ({}) },
  expanded: { type: Boolean, default: false },
  // True once the parent's lazy subtree fetch has settled. Lets us tell
  // "still loading" from "loaded but nothing captured" — the discarded turns
  // can advertise a count yet have no backing spans in the trace store.
  loaded: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'toggle', 'activate'])

const selected = computed(() => {
  const sel = props.ctx?.selectedSpan
  return !!(sel && sel.span_id === props.span.span_id)
})
const promptCount = computed(() => (props.span.attributes || {}).abandoned_prompt_count || 0)
const fileCount = computed(() => (props.span.attributes || {}).rolled_back_count || 0)
// A rewind always discards a branch; gate the toggle on either count so the
// button shows even before the subtree (and its real span count) has loaded.
const hasDiscarded = computed(() => {
  const a = props.span.attributes || {}
  return (a.abandoned_prompt_count || 0) > 0 || (a.abandoned_span_count || 0) > 0
})

const showFiles = ref(false)
const files = ref(null)
const loading = ref(false)
const error = ref('')
const openPath = ref('')

async function toggleFiles() {
  showFiles.value = !showFiles.value
  if (!showFiles.value || files.value !== null) return
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/sessions/${props.traceId}/spans/${props.span.span_id}/rewind`)
    files.value = data.files || []
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    loading.value = false
  }
}

function togglePath(p) {
  openPath.value = openPath.value === p ? '' : p
}

function baseName(p) {
  return p ? p.split('/').pop() : ''
}
</script>

<template>
  <div
    class="my-3 cursor-pointer group rounded transition-colors hover:bg-rose-50/60 focus-visible:outline-2 focus-visible:outline-rose-400"
    :class="selected ? 'ring-2 ring-rose-300' : ''"
    tabindex="0"
    role="button"
    @click="emit('select', span)"
    @keydown.enter.prevent="emit('select', span)"
    @keydown.space.prevent="emit('select', span)"
  >
    <div class="flex items-center gap-2 text-rose-700">
      <div class="flex-1 border-t border-dashed border-rose-300"></div>
      <span class="inline-flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-wider whitespace-nowrap px-2 py-0.5 rounded bg-rose-50 border border-rose-200">
        <span>↩ rewound</span>
        <template v-if="promptCount">
          <span class="text-rose-400">·</span>
          <span class="lowercase">{{ promptCount }} prompt{{ promptCount === 1 ? '' : 's' }} discarded</span>
        </template>
        <template v-if="fileCount">
          <span class="text-rose-400">·</span>
          <span class="lowercase">{{ fileCount }} file{{ fileCount === 1 ? '' : 's' }} rolled back</span>
        </template>
        <span class="text-rose-400">·</span>
        <span class="text-rose-600 normal-case">{{ fmtClock(span.start_time) }}</span>
      </span>
      <div class="flex-1 border-t border-dashed border-rose-300"></div>
    </div>

    <div v-if="hasDiscarded || fileCount" class="mt-1 flex justify-center gap-4">
      <button
        v-if="hasDiscarded"
        type="button"
        class="text-[11px] text-rose-600 hover:text-rose-700 hover:underline cursor-pointer focus-visible:outline-2 focus-visible:outline-rose-400 rounded"
        @click.stop="emit('toggle')"
      >{{ expanded ? 'hide discarded turns ▴' : 'show discarded turns ▾' }}</button>
      <button
        v-if="fileCount"
        type="button"
        class="text-[11px] text-rose-600 hover:text-rose-700 hover:underline cursor-pointer focus-visible:outline-2 focus-visible:outline-rose-400 rounded"
        @click.stop="toggleFiles"
      >{{ showFiles ? 'hide rolled-back files ▴' : 'show rolled-back files ▾' }}</button>
    </div>

    <div v-if="showFiles" class="mt-1 px-4 text-[11px]">
      <div v-if="loading" class="text-slate-400 text-center py-1">loading…</div>
      <div v-else-if="error" class="text-red-500 text-center py-1">{{ error }}</div>
      <div v-else class="space-y-1">
        <div v-for="f in files" :key="f.path" class="border border-rose-100 rounded">
          <button
            type="button"
            class="w-full flex items-center gap-2 px-2 py-1 text-left hover:bg-rose-50/60 focus-visible:outline-2 focus-visible:outline-rose-400 rounded"
            @click.stop="togglePath(f.path)"
          >
            <span class="text-rose-500">{{ openPath === f.path ? '▾' : '▸' }}</span>
            <span class="font-mono text-slate-700 truncate" :title="f.path">{{ baseName(f.path) }}</span>
            <span class="text-slate-400 truncate">{{ f.path }}</span>
          </button>
          <div v-if="openPath === f.path" class="grid grid-cols-1 sm:grid-cols-2 gap-2 p-2 border-t border-rose-100">
            <div>
              <div class="text-rose-600 mb-0.5">before (discarded)</div>
              <pre class="whitespace-pre-wrap break-words bg-rose-50/40 rounded px-2 py-1 max-h-[40vh] overflow-y-auto">{{ f.before_text ?? '(file absent)' }}</pre>
            </div>
            <div>
              <div class="text-emerald-700 mb-0.5">after (restored)</div>
              <pre class="whitespace-pre-wrap break-words bg-emerald-50/40 rounded px-2 py-1 max-h-[40vh] overflow-y-auto">{{ f.after_text ?? '(file absent)' }}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Discarded branch: the abandoned prompt + the work the rewind threw away.
       Hidden by default. A sibling of the clickable card (not nested in it) so
       selecting a discarded row picks that span, not the marker. The rows are
       the same cards as the live conversation, dimmed behind a rose rail. -->
  <div
    v-if="expanded"
    class="mt-2 ml-3 pl-3 border-l-2 border-dashed border-rose-200 space-y-1 opacity-80"
  >
    <div v-if="!descendants.length" class="text-slate-400 text-[11px] py-1">
      {{ loaded ? 'discarded turns were not captured in the trace' : 'loading discarded turns…' }}
    </div>
    <template v-for="d in descendants" :key="d.span.span_id">
      <div
        v-if="d.span.name === 'prompt'"
        class="rounded border border-rose-200 bg-rose-50/40 px-2 py-1"
      >
        <div class="text-rose-600 font-mono uppercase tracking-wider text-[10px] mb-0.5">discarded prompt</div>
        <PromptBody
          v-if="d.span.attributes?.text"
          :text="d.span.attributes.text"
          :trace-id="traceId"
          :span-id="d.span.span_id"
          :image-indices="d.span.attributes?.image_indices || []"
        />
      </div>
      <ConversationSpanCard
        v-else
        :span="d.span"
        :selected-span="ctx.selectedSpan"
        :folding="ctx.folding"
        :agent-merge="ctx.agentMerge"
        :workflow-runs-by-id="ctx.workflowRunsById"
        @activate="emit('activate', $event)"
      />
    </template>
  </div>
</template>
