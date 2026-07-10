<script setup>
// "Agents" roster button + pick-to-scope popover for the desktop trace
// header — the reduced desktop analogue of live/LiveAgentSheet.vue (no
// detail pane; the primary click scopes). Running agents first (waiting
// rides that group, amber), then Finished (incl. interrupted/stale — still
// scopeable). Button absent when the roster is empty.
import { ref, watch, onUnmounted } from 'vue'
import Button from './ui/Button.vue'
import { agentStatusLabel } from '../utils/liveRows.js'
import { fmtElapsedSeconds } from '../utils/traceFormatters.js'
import { agentElapsedSeconds } from '../composables/useAgentElapsed.js'

const props = defineProps({
  runningAgents: { type: Array, default: () => [] },
  finishedAgents: { type: Array, default: () => [] },
  // running + waiting count for the badge (useLiveAgents.runningCount).
  runningCount: { type: Number, default: 0 },
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
})
const emit = defineEmits(['scope'])

const open = ref(false)
const rootEl = ref(null)

function onDocClick(e) {
  if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false
}
function onDocKeydown(e) {
  if (e.key === 'Escape') open.value = false
}
function unbindDoc() {
  document.removeEventListener('mousedown', onDocClick)
  document.removeEventListener('keydown', onDocKeydown)
}
watch(open, (on) => {
  if (on) {
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onDocKeydown)
  } else {
    unbindDoc()
  }
})
onUnmounted(unbindDoc)

function pick(agent) {
  open.value = false
  emit('scope', agent.agentId)
}

// Ticking elapsed for running rows, server-clock anchored (useAgentElapsed).
const nowMs = ref(Date.now())
let tick = null
watch(() => open.value && props.runningAgents.length > 0, (needsTick) => {
  if (tick) { clearInterval(tick); tick = null }
  if (needsTick) {
    nowMs.value = Date.now()
    tick = setInterval(() => { nowMs.value = Date.now() }, 1000)
  }
}, { immediate: true })
onUnmounted(() => { if (tick) clearInterval(tick) })

function statusOf(agent) {
  const secs = agentElapsedSeconds(
    agent.startTime, props.serverNow, props.serverNowAt, nowMs.value)
  const elapsed = Number.isFinite(secs) ? fmtElapsedSeconds(secs) : ''
  return agentStatusLabel(agent, elapsed, { compact: true })
}
</script>

<template>
  <div
    v-if="runningAgents.length || finishedAgents.length"
    ref="rootEl"
    class="relative"
  >
    <Button
      variant="ghost"
      class="gap-1.5 px-3 py-1 h-auto rounded-full border border-slate-200 bg-white text-xs text-slate-600 hover:bg-violet-50 hover:border-violet-300 hover:text-violet-700"
      data-testid="trace-agents-btn"
      aria-haspopup="true"
      :aria-expanded="open"
      @click="open = !open"
    >
      <span class="text-violet-500" aria-hidden="true">◈</span>
      Agents
      <span
        v-if="runningCount"
        class="inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded-full bg-violet-500 text-white text-[10px] tabular-nums"
        data-testid="trace-agents-badge"
      >{{ runningCount }}</span>
    </Button>
    <div
      v-if="open"
      class="absolute right-0 top-full mt-1.5 z-30 w-80 max-h-96 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg p-1.5 text-left"
      data-testid="trace-agents-popover"
      role="menu"
    >
      <div class="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">Running</div>
      <div v-if="!runningAgents.length" class="px-2 pb-1.5 text-[11px] text-slate-400">no agents running</div>
      <Button
        v-for="a in runningAgents"
        :key="a.spanId"
        variant="ghost"
        class="w-full h-auto justify-start gap-2 px-2 py-1.5 rounded-md text-left hover:bg-violet-50"
        data-testid="trace-agents-item"
        :aria-label="`Scope the view to ${a.agentType}`"
        @click="pick(a)"
      >
        <span
          class="inline-block w-1.5 h-1.5 rounded-full shrink-0 animate-pulse"
          :class="a.status === 'waiting' ? 'bg-amber-500' : 'bg-violet-500'"
          aria-hidden="true"
        ></span>
        <span class="flex-1 min-w-0">
          <span class="block text-xs font-medium text-slate-800">{{ a.agentType }}</span>
          <span class="block text-[11px] text-slate-500 truncate">{{ a.description }}</span>
        </span>
        <span class="font-mono text-[10px] text-slate-400 shrink-0">{{ statusOf(a) }}</span>
      </Button>
      <template v-if="finishedAgents.length">
        <div class="px-2 pt-1.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 border-t border-slate-100 mt-1">Finished</div>
        <Button
          v-for="a in finishedAgents"
          :key="a.spanId"
          variant="ghost"
          class="w-full h-auto justify-start gap-2 px-2 py-1.5 rounded-md text-left hover:bg-violet-50"
          data-testid="trace-agents-item"
          :aria-label="`Scope the view to ${a.agentType}`"
          @click="pick(a)"
        >
          <span class="inline-block w-1.5 h-1.5 rounded-full shrink-0 bg-slate-300" aria-hidden="true"></span>
          <span class="flex-1 min-w-0">
            <span class="block text-xs font-medium text-slate-600">{{ a.agentType }}</span>
            <span class="block text-[11px] text-slate-400 truncate">{{ a.resultPreview || a.description }}</span>
          </span>
          <span class="font-mono text-[10px] text-slate-400 shrink-0">{{ statusOf(a) }}</span>
        </Button>
      </template>
    </div>
  </div>
</template>
