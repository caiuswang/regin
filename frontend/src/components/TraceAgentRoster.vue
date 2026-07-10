<script setup>
// The running-first agent roster list, shared by the <xl TraceAgentsPopover
// (320px corner menu) and the ≥xl TraceAgentPane roster mode (fills the pane).
// Running agents first (waiting rides that group, amber), then Finished
// (incl. interrupted/stale — still scopeable). Primary click on a row scopes.
import { ref, watch, onUnmounted } from 'vue'
import Button from './ui/Button.vue'
import { agentStatusLabel } from '../utils/liveRows.js'
import { fmtElapsedSeconds } from '../utils/traceFormatters.js'
import { agentElapsedSeconds } from '../composables/useAgentElapsed.js'

const props = defineProps({
  runningAgents: { type: Array, default: () => [] },
  finishedAgents: { type: Array, default: () => [] },
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
  // Tick running-agent elapsed labels only while the list is actually shown.
  active: { type: Boolean, default: true },
})
const emit = defineEmits(['pick'])

// Ticking elapsed for running rows, server-clock anchored (useAgentElapsed).
const nowMs = ref(Date.now())
let tick = null
watch(() => props.active && props.runningAgents.length > 0, (needsTick) => {
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
  <div>
    <div class="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
      Running · {{ runningAgents.length }}
    </div>
    <div v-if="!runningAgents.length" class="px-2 pb-1.5 text-[11px] text-slate-400">no agents running</div>
    <Button
      v-for="a in runningAgents"
      :key="a.spanId"
      variant="ghost"
      class="w-full h-auto justify-start gap-2 px-2 py-1.5 rounded-md text-left hover:bg-violet-50"
      data-testid="trace-agents-item"
      :aria-label="`Scope the view to ${a.agentType}`"
      @click="emit('pick', a)"
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
      <div class="px-2 pt-1.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400 border-t border-slate-100 mt-1">
        Finished · {{ finishedAgents.length }}
      </div>
      <Button
        v-for="a in finishedAgents"
        :key="a.spanId"
        variant="ghost"
        class="w-full h-auto justify-start gap-2 px-2 py-1.5 rounded-md text-left hover:bg-violet-50"
        data-testid="trace-agents-item"
        :aria-label="`Scope the view to ${a.agentType}`"
        @click="emit('pick', a)"
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
</template>
