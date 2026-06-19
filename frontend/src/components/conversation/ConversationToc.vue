<script setup>
import { computed, watch, nextTick, ref } from 'vue'
import { fmtTime, fmtDuration, fmtTokens, fmtModel, truncate } from '../../utils/traceFormatters.js'
import { useStickyMaxHeight } from '../../composables/useStickyMaxHeight.js'
import { useConversationRail } from '../../composables/useConversationRail.js'
import Button from '../ui/Button.vue'

// Left-rail table of contents for the conversation spine. Two modes:
//   • regular session → a flat list of turns (prompt previews + token meta)
//   • workflow run     → phase sections, each listing its agents under a rail
// Selection is delegated upward (`select-turn` / `select-workflow-row`) because
// the scroll targets (promptRefs / spanRefs) live in the orchestrator; the rail
// owns only its own scroll position (`tocScrollEl`) and the active-turn
// auto-follow (`turnTocRefs` + the activeTurnIdx watcher are rail-local).
const props = defineProps({
  isWorkflow: { type: Boolean, default: false },
  hasPhaseSpans: { type: Boolean, default: false },
  phaseItems: { type: Array, default: () => [] },
  phasePlan: { type: Array, default: () => [] },
  turnItems: { type: Array, default: () => [] },
  selectedSpan: { type: Object, default: null },
  foldableAgentIds: { type: Array, default: () => [] },
  allAgentsExpanded: { type: Boolean, default: false },
})
const emit = defineEmits(['select-turn', 'select-workflow-row', 'jump-live', 'expand-all-agents', 'collapse-all-agents'])

const { railWidth, onRailResizeStart, onRailResizeKey } = useConversationRail()

// The Turns TOC rail is `position: sticky`. useStickyMaxHeight keeps its
// max-height fitted to the viewport in both natural and stuck positions.
const turnsAsideEl = ref(null)
const { maxH: turnsMaxH } = useStickyMaxHeight(turnsAsideEl)

// TOC scroll region + one ref per turn card (active-turn auto-follow).
const tocScrollEl = ref(null)
const turnTocRefs = new Map()

// Active turn = the turn whose [start, end] window contains the parent's
// `selectedSpan`. Falls back to a direct prompt-id match.
const activeTurnIdx = computed(() => {
  if (!props.selectedSpan || !props.turnItems.length) return -1
  const direct = props.turnItems.findIndex(t => t.promptSpanId === props.selectedSpan.span_id)
  if (direct >= 0) return direct
  const t = props.selectedSpan.start_time
    ? new Date(props.selectedSpan.start_time).getTime() : null
  if (t == null) return -1
  for (let i = 0; i < props.turnItems.length; i++) {
    const item = props.turnItems[i]
    if (t >= item.startMs && t <= item.endMs) return i
  }
  return -1
})

// Keep the active turn visible inside the TOC's own scroll region. Uses
// `block: 'nearest'` so we only nudge when the highlighted card is off-screen —
// the user's manual scroll position is preserved while browsing.
watch(activeTurnIdx, async (idx) => {
  if (idx < 0) return
  await nextTick()
  const item = props.turnItems[idx]
  if (!item) return
  const el = turnTocRefs.get(item.promptSpanId)
  if (el && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
})

// Snap the rail to its bottom, then ask the orchestrator to jump the chat.
function jumpToLive() {
  if (tocScrollEl.value) tocScrollEl.value.scrollTop = tocScrollEl.value.scrollHeight
  emit('jump-live')
}
</script>

<template>
  <!-- Flex column so the items area scrolls *inside* the aside while the
       header and "Jump to live" footer stay pinned. -->
  <aside
    ref="turnsAsideEl"
    class="shrink-0 sticky self-start flex flex-col"
    :style="{
      width: railWidth + 'px',
      top: 'calc(var(--regin-trace-header-h, 5rem) + 0.5rem)',
      maxHeight: turnsMaxH || 'calc(100vh - var(--regin-trace-header-h, 5rem) - 2rem)',
    }"
  >
    <!-- Drag handle on the rail's right edge (in the gutter). A <button> so
         it's keyboard focusable (arrow keys resize). -->
    <button
      type="button"
      class="absolute top-0 -right-1.5 w-3 h-full p-0 bg-transparent border-0 cursor-col-resize group z-10 select-none focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 rounded"
      title="Drag (or ←/→) to resize panel"
      aria-label="Resize panel"
      @mousedown="onRailResizeStart"
      @keydown="onRailResizeKey"
    >
      <span class="block mx-auto w-px h-full bg-slate-200 group-hover:bg-blue-400 transition-colors"></span>
    </button>
    <div class="flex items-baseline justify-between mb-2 shrink-0">
      <h3 class="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">{{ isWorkflow ? 'Phases' : 'Turns' }}</h3>
      <div class="flex items-baseline gap-2">
        <Button
          v-if="foldableAgentIds.length"
          variant="link"
          size="sm"
          class="text-[11px]"
          @click="allAgentsExpanded ? $emit('collapse-all-agents') : $emit('expand-all-agents')"
        >{{ allAgentsExpanded ? 'collapse all' : 'expand all' }}</Button>
        <span class="text-[11px] text-slate-400 tabular-nums">{{ isWorkflow ? (hasPhaseSpans ? phaseItems.length : (phasePlan.length || phaseItems.length)) : turnItems.length }}</span>
      </div>
    </div>
    <!-- Workflow phase TOC -->
    <div
      v-if="isWorkflow"
      ref="tocScrollEl"
      class="flex-1 min-h-0 overflow-y-auto pr-1 space-y-3 [scrollbar-gutter:stable] [scrollbar-width:thin] [overscroll-behavior:contain]"
    >
      <!-- Declared phase plan (live runs only): manifest phaseIndex isn't
           written until completion, so surface the script's planned phases. -->
      <div
        v-if="!hasPhaseSpans && phasePlan.length"
        class="rounded-md border border-dashed border-slate-200 bg-slate-50/60 px-2 py-1.5"
      >
        <div class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold mb-1">Planned phases</div>
        <div class="space-y-1">
          <div v-for="(ph, i) in phasePlan" :key="i" class="flex items-start gap-1.5">
            <span class="mt-px inline-flex items-center justify-center shrink-0 w-4 h-4 rounded bg-slate-200 text-slate-500 text-[10px] font-bold tabular-nums">{{ i + 1 }}</span>
            <div class="min-w-0 flex-1">
              <div class="text-[11px] font-medium text-slate-500 leading-tight truncate" :title="ph.detail || ph.title">{{ ph.title }}</div>
            </div>
          </div>
        </div>
        <div class="text-[10px] text-slate-400 italic mt-1">agents grouped by phase on completion</div>
      </div>
      <div v-for="p in phaseItems" :key="p.phaseSpanId">
        <!-- Running band header (live: agents not yet phase-mapped) -->
        <div v-if="p.running" class="px-1.5 py-1">
          <div class="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-400 font-semibold">
            <span class="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse shrink-0"></span>
            <span>Running</span>
            <span class="ml-auto normal-case tracking-normal tabular-nums" title="finished / total agents">{{ p.doneCount }}/{{ p.agentCount }} agents</span>
          </div>
        </div>
        <!-- Phase header (completed runs: real phases) -->
        <div
          v-else
          class="cursor-pointer rounded-md px-1.5 py-1 transition-colors hover:bg-emerald-50/70"
          :class="selectedSpan && selectedSpan.span_id === p.phaseSpanId ? 'bg-emerald-50 ring-1 ring-emerald-200' : ''"
          @click="$emit('select-workflow-row', p.phaseSpanId)"
        >
          <div class="flex items-start gap-1.5">
            <span
              class="mt-px inline-flex items-center justify-center shrink-0 w-4 h-4 rounded text-[10px] font-bold tabular-nums"
              :class="p.complete ? 'bg-emerald-100 text-emerald-700'
                : p.agentCount ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-400'"
            >{{ p.complete ? '✓' : p.index }}</span>
            <div class="min-w-0 flex-1">
              <div class="text-xs font-semibold text-slate-800 leading-tight flex items-center gap-1.5">
                <span class="truncate" :title="p.title">{{ p.title }}</span>
                <span
                  v-if="p.inProgress"
                  class="inline-block w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse shrink-0"
                  title="phase in progress"
                ></span>
              </div>
              <div v-if="p.detail" class="text-[10px] text-slate-400 leading-snug truncate" :title="p.detail">{{ p.detail }}</div>
              <div class="mt-0.5 flex items-center gap-1 text-[10px] text-slate-400 leading-tight">
                <span v-if="p.agentCount" class="font-medium tabular-nums" title="finished / total agents">{{ p.doneCount }}/{{ p.agentCount }} agents</span>
                <span v-else class="font-medium text-slate-300">pending</span>
                <template v-if="p.tokens">
                  <span class="text-slate-300">·</span>
                  <span class="tabular-nums" title="total output tokens across this phase's agents">{{ fmtTokens(p.tokens) }}</span>
                </template>
              </div>
            </div>
          </div>
        </div>
        <!-- Agents under this phase (connector rail on the left). -->
        <div v-if="p.agents.length" class="mt-1 ml-2.5 pl-2.5 border-l border-slate-200 space-y-0.5">
          <div
            v-for="a in p.agents"
            :key="a.spanId"
            class="cursor-pointer rounded px-1.5 py-1 transition-colors hover:bg-slate-50"
            :class="selectedSpan && selectedSpan.span_id === a.spanId ? 'bg-blue-50 ring-1 ring-blue-200' : ''"
            @click="$emit('select-workflow-row', a.spanId)"
          >
            <div
              class="flex items-center gap-1.5 text-[11px] leading-tight"
              :class="a.done ? 'text-slate-600' : a.running ? 'text-blue-700 font-semibold' : 'text-slate-400'"
            >
              <span
                v-if="a.done"
                class="shrink-0 w-2 text-center text-emerald-500 text-[10px] leading-none"
                title="done"
              >✓</span>
              <span
                v-else-if="a.running"
                class="inline-block w-2 h-2 rounded-full shrink-0 bg-blue-500 ring-2 ring-blue-200 animate-pulse"
                title="running"
              ></span>
              <span
                v-else
                class="inline-block w-2 h-2 rounded-full border border-slate-300 shrink-0"
                :title="a.state || 'queued'"
              ></span>
              <span class="truncate">{{ a.label }}</span>
            </div>
            <div
              v-if="a.model || a.tokens || a.toolCalls"
              class="ml-3 mt-0.5 flex flex-wrap items-center gap-x-1 gap-y-0.5 text-[10px] text-slate-400 leading-tight"
            >
              <span
                v-if="a.model"
                class="inline-flex items-center rounded bg-slate-100 text-slate-500 px-1 font-medium"
                :title="a.model"
              >{{ fmtModel(a.model) }}</span>
              <span v-if="a.tokens" class="tabular-nums" title="output tokens">{{ fmtTokens(a.tokens) }}</span>
              <template v-if="a.toolCalls">
                <span class="text-slate-300">·</span>
                <span class="tabular-nums">{{ a.toolCalls }} tool<span v-if="a.toolCalls !== 1">s</span></span>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div v-else-if="!turnItems.length" class="text-xs text-slate-400">
      No prompts found.
    </div>
    <div
      v-else
      ref="tocScrollEl"
      class="flex-1 min-h-0 overflow-y-auto pr-1 space-y-1 [scrollbar-gutter:stable] [scrollbar-width:thin] [overscroll-behavior:contain]"
    >
      <div
        v-for="item in turnItems"
        :key="item.promptSpanId"
        :ref="(el) => { if (el) turnTocRefs.set(item.promptSpanId, el) }"
        tabindex="0"
        class="cursor-pointer rounded-md px-2 py-1.5 border transition-colors hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="activeTurnIdx === item.idx
          ? 'bg-blue-50 border-blue-300'
          : 'bg-white border-transparent hover:border-slate-200'"
        @click="$emit('select-turn', item)"
      >
        <div class="flex items-baseline gap-1.5 text-xs leading-tight">
          <span class="text-slate-400 font-mono shrink-0 tabular-nums">#{{ item.idx + 1 }}</span>
          <span
            class="truncate"
            :class="activeTurnIdx === item.idx ? 'font-medium text-slate-900' : 'text-slate-700'"
          >{{ truncate(item.promptText, 32) }}</span>
        </div>
        <div class="text-[10px] text-slate-400 font-mono mt-0.5 flex items-center gap-1.5">
          <span>{{ fmtTime(item.timestamp) }}</span>
          <span v-if="item.durationMs" class="text-slate-300">·</span>
          <span v-if="item.durationMs">{{ fmtDuration(item.durationMs) }}</span>
          <span v-if="item.turnAgg?.inputTokens" class="text-slate-300">·</span>
          <span v-if="item.turnAgg?.inputTokens">↑{{ fmtTokens(item.turnAgg.inputTokens) }}</span>
        </div>
      </div>
    </div>
    <Button
      variant="link"
      size="sm"
      class="shrink-0 block mt-2 pt-2 border-t border-slate-100 text-[11px]"
      title="Scroll to the most recent turn"
      @click="jumpToLive"
    >↓ Jump to live</Button>
  </aside>
</template>
