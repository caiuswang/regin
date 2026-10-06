<script setup>
import { computed, inject, ref } from 'vue'
import {
  fmtDuration, fmtBytes, fullLabel, bashEditSummary, hasBashEdits, backgroundShell,
} from '../../../utils/traceFormatters.js'
import CopyButton from './CopyButton.vue'
import BashEditDiffs from './BashEditDiffs.vue'
import { useAgentElapsed } from '../../../composables/useAgentElapsed.js'

// Bash row: flat one-liner like other inline tool rows when collapsed, led by
// a `Shell` verb. Output expands into a dark terminal-themed panel; the
// disclosure caret sits on the right, so every row's title stays left-aligned.
const props = defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  // useConversationFolding: { bashExpanded, toggleBashExpanded }
  folding: { type: Object, required: true },
})
defineEmits(['activate'])

const bg = computed(() => backgroundShell(props.span?.attributes))

// A backgrounded call returns instantly with nothing captured, so its panel is
// the launch record itself — the command plus how the shell ended.
const hasBody = computed(() => {
  const a = props.span?.attributes || {}
  return Boolean(a.command || a.stdout || a.stderr || hasBashEdits(a) || bg.value)
})

// The status is whatever word the harness's <status> tag carried, so an
// unrecognised one must still render — amber, the same "needs a look" tint as
// an interrupted call.
const BG_UNKNOWN_TINT = 'bg-amber-100 border-amber-200 text-amber-800'
const BG_TINTS = {
  launching: 'bg-sky-100 border-sky-200 text-sky-800',
  running: 'bg-sky-100 border-sky-200 text-sky-800',
  completed: 'bg-slate-100 border-slate-200 text-slate-600',
  failed: 'bg-red-100 border-red-200 text-red-800',
}
const bgTint = computed(() => BG_TINTS[bg.value?.status] || BG_UNKNOWN_TINT)

// A shell with no completion has only its launch stamp, so its elapsed time
// has to be counted rather than read off a recorded duration. Same server-
// anchored formula and 1s tick every other live surface uses; it stops the
// moment a completion lands and the recorded figure takes over.
const clock = inject('traceServerClock', ref({ now: '', at: 0 }))
const bgUnfinished = computed(() =>
  bg.value?.status === 'running' || bg.value?.status === 'launching')
const bgTicking = useAgentElapsed(
  () => props.span?.start_time || '',
  () => clock.value.now,
  () => clock.value.at,
  () => bgUnfinished.value,
)
const bgElapsed = computed(() => (
  bgUnfinished.value ? bgTicking.value : fmtDuration(bg.value?.elapsedMs || 0)
))

const edits = computed(() => bashEditSummary(props.span?.attributes))

// The server only stamps `command` when it exceeds the preview cap (to avoid
// duplicating the text on the span); a command at or under that cap has no
// `command` attribute at all, and `command_preview` is the whole thing.
const commandText = computed(() => {
  const a = props.span?.attributes || {}
  return a.command || a.command_preview || ''
})

// When the call changed files, the change is what the card is for and the
// output is its trace — so the terminal panel opens on the diffs and keeps the
// result one click away. Nullable override rather than a ref seeded at setup:
// a live span acquires its edits after the card is already mounted, and a seed
// read once would fold on whatever the span happened to carry first.
const outputOverride = ref(null)
const outputOpen = computed(() => (
  outputOverride.value === null ? !edits.value.count : outputOverride.value
))
function toggleOutput() { outputOverride.value = !outputOpen.value }
</script>

<template>
  <!-- Same open/closed split as DiffCard: open, the row is the card's header
       bar and the terminal body attaches to it inside one bordered unit. -->
  <div
    class="group"
    :class="[
      folding.bashExpanded(span.span_id) ? 'rounded-[9px] border border-slate-200 overflow-hidden' : '',
      folding.bashExpanded(span.span_id) && selectedSpan && selectedSpan.span_id === span.span_id
        ? 'border-blue-300' : '',
    ]"
  >
    <div
      tabindex="0"
      class="flex items-baseline gap-[9px] cursor-pointer px-2.5 py-[5px] focus-visible:outline-2 focus-visible:outline-blue-500"
      :class="[
        folding.bashExpanded(span.span_id)
          ? 'bg-slate-100 border-b border-slate-200 px-3 py-[6px]'
          : 'rounded-lg border border-transparent hover:bg-slate-50',
        !folding.bashExpanded(span.span_id) && selectedSpan && selectedSpan.span_id === span.span_id
          ? 'event-selected' : '',
      ]"
      @click="$emit('activate', span); hasBody && folding.toggleBashExpanded(span.span_id)"
    >
      <span class="text-[12px] font-semibold text-slate-600 shrink-0">Shell</span>
      <span
        class="font-mono text-[11.5px] flex-1 min-w-0 truncate"
        :class="folding.bashExpanded(span.span_id) ? 'text-slate-900' : 'text-slate-500'"
      >{{ span.attributes?.command_preview || fullLabel(span) }}</span>
      <span
        v-if="span.attributes?.interrupted"
        class="text-[10px] bg-amber-100 border border-amber-200 text-amber-800 px-1 rounded shrink-0"
      >interrupted</span>
      <span
        v-if="bg"
        class="text-[10px] border px-1 rounded shrink-0"
        :class="bgTint"
        data-bash-background
        :title="bg.summary || `background task ${bg.taskId || '(not yet assigned)'}`"
      >background · {{ bg.status }}</span>
      <!-- The shell's own elapsed time, not the call's: `duration_ms` beside it
           is the milliseconds the tool took to hand back. -->
      <span
        v-if="bg && bgElapsed"
        class="font-mono text-[10.5px] text-sky-600 shrink-0"
        data-bash-background-elapsed
        :title="bgUnfinished ? 'running for this long' : 'the shell ran for this long'"
      >{{ bgElapsed }}</span>
      <span
        v-if="span.attributes?.return_code_interpretation"
        class="text-[11px] text-slate-400 italic truncate shrink-0 max-w-[35%]"
      >{{ span.attributes.return_code_interpretation }}</span>
      <span
        v-if="edits.count"
        class="font-mono text-[10.5px] text-orange-600 shrink-0"
        data-bash-edit-count
      >{{ edits.label }}</span>
      <span v-if="edits.added" class="font-mono text-[10.5px] text-emerald-600 shrink-0">+{{ edits.added }}</span>
      <span v-if="edits.removed" class="font-mono text-[10.5px] text-red-600 shrink-0">−{{ edits.removed }}</span>
      <span
        v-if="span.duration_ms"
        class="font-mono text-[10.5px] text-slate-400 shrink-0"
        data-tool-duration
        :title="bg ? 'the tool call itself — how long before the shell was handed back'
          : 'how long the command took'"
      >{{ fmtDuration(span.duration_ms) }}</span>
      <!-- Only when there is a body to reveal: an `interrupted`-only Bash span
           carries no command or output, and its caret opened an empty box. -->
      <span
        v-if="hasBody"
        class="text-[10.5px] text-slate-400 shrink-0 select-none w-3 text-center"
      >{{ folding.bashExpanded(span.span_id) ? '▾' : '▸' }}</span>
    </div>
    <div
      v-if="folding.bashExpanded(span.span_id)"
      class="code-surface bg-slate-900"
    >
      <div v-if="commandText" class="px-3 py-2">
        <div class="flex items-center gap-2 mb-1">
          <button
            type="button"
            class="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-emerald-400 hover:text-emerald-200 focus-visible:outline-2 focus-visible:outline-blue-500"
            data-bash-output-toggle
            :aria-expanded="outputOpen"
            @click.stop="toggleOutput"
          ><span class="w-2 text-center select-none">{{ outputOpen ? '▾' : '▸' }}</span>command</button>
          <span
            v-if="span.attributes.command_truncated_bytes"
            class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
          >truncated {{ fmtBytes(span.attributes.command_truncated_bytes) }}</span>
          <CopyButton :text="commandText" />
        </div>
        <pre
          v-if="outputOpen"
          data-bash-output
          class="text-[12px] text-emerald-200 whitespace-pre-wrap break-words font-mono leading-snug max-h-96 overflow-auto"
        >{{ commandText }}</pre>
      </div>
      <div
        v-if="span.attributes?.stdout"
        class="px-3 py-2"
        :class="commandText ? 'border-t border-slate-800' : ''"
      >
        <div class="flex items-center gap-2 mb-1">
          <button
            type="button"
            class="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 hover:text-slate-200 focus-visible:outline-2 focus-visible:outline-blue-500"
            data-bash-output-toggle
            :aria-expanded="outputOpen"
            @click.stop="toggleOutput"
          ><span class="w-2 text-center select-none">{{ outputOpen ? '▾' : '▸' }}</span>stdout</button>
          <span
            v-if="span.attributes.stdout_truncated_bytes"
            class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
          >truncated {{ fmtBytes(span.attributes.stdout_truncated_bytes) }}</span>
          <CopyButton :text="span.attributes.stdout" />
        </div>
        <pre
          v-if="outputOpen"
          data-bash-output
          class="text-[12px] text-slate-100 whitespace-pre-wrap break-words font-mono leading-snug max-h-96 overflow-auto"
        >{{ span.attributes.stdout }}</pre>
      </div>
      <div
        v-if="span.attributes?.stderr"
        class="px-3 py-2"
        :class="(span.attributes?.stdout || commandText) ? 'border-t border-slate-800' : ''"
      >
        <div class="flex items-center gap-2 mb-1">
          <button
            type="button"
            class="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-red-400 hover:text-red-200 focus-visible:outline-2 focus-visible:outline-blue-500"
            data-bash-output-toggle
            :aria-expanded="outputOpen"
            @click.stop="toggleOutput"
          ><span class="w-2 text-center select-none">{{ outputOpen ? '▾' : '▸' }}</span>stderr</button>
          <span
            v-if="span.attributes.stderr_truncated_bytes"
            class="text-[10px] text-amber-300 bg-amber-900/40 border border-amber-700/60 px-1 rounded"
          >truncated {{ fmtBytes(span.attributes.stderr_truncated_bytes) }}</span>
          <CopyButton :text="span.attributes.stderr" tint="text-red-400 hover:bg-red-900/40" />
        </div>
        <pre
          v-if="outputOpen"
          data-bash-output
          class="text-[12px] text-red-300 whitespace-pre-wrap break-words font-mono leading-snug max-h-64 overflow-auto"
        >{{ span.attributes.stderr }}</pre>
      </div>
      <div
        v-if="bg"
        class="px-3 py-2 border-t border-slate-800"
        data-bash-background-detail
      >
        <div class="text-[10px] font-semibold uppercase tracking-wider text-sky-400 mb-1">background shell</div>
        <div class="text-[12px] text-slate-300 font-mono leading-snug break-words">
          <div v-if="bg.summary">{{ bg.summary }}</div>
          <div v-else class="text-slate-500">no completion reported yet</div>
          <div v-if="bg.outputFile" class="mt-1 flex items-center gap-2">
            <span class="text-slate-400 break-all">{{ bg.outputFile }}</span>
            <CopyButton :text="bg.outputFile" />
          </div>
        </div>
      </div>
      <BashEditDiffs
        v-if="edits.count"
        :attrs="span.attributes"
        :trace-id="span.trace_id || ''"
        :divided="Boolean(commandText || span.attributes?.stdout || span.attributes?.stderr || bg)"
      />
    </div>
  </div>
</template>
