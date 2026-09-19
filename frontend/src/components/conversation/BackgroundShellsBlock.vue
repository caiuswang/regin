<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { fmtElapsedSeconds } from '../../utils/traceFormatters.js'
import { agentElapsedSeconds } from '../../composables/useAgentElapsed.js'
import api from '../../api'

// The running background shells, kept at the FOOT of the feed rather than
// left where they were launched. A shell outlives the turn that started it,
// so by the time it matters its card has scrolled out of reach — and more
// than one can be running, which is why this is a list and not a status row.
//
// Whole-session by construction: the entries come from the server's
// `background_shells` roster, not from the loaded page, so a shell that has
// been running for half an hour is still listed.
const props = defineProps({
  // { traceId, shells } — one binding; this feed's template is near its
  // directive budget.
  live: { type: Object, default: () => ({}) },
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
})

const running = computed(() =>
  (props.live?.shells || []).filter(s => s.status === 'running'))

const open = ref(true)
const expanded = ref(null)
const output = ref({})

const nowMs = ref(Date.now())
let tick = null
watch(() => running.value.length > 0, (on) => {
  if (tick) { clearInterval(tick); tick = null }
  if (on) {
    nowMs.value = Date.now()
    tick = setInterval(() => { nowMs.value = Date.now() }, 1000)
  }
}, { immediate: true })

function elapsed(shell) {
  const secs = agentElapsedSeconds(
    shell.started_at, props.serverNow, props.serverNowAt, nowMs.value)
  return Number.isFinite(secs) ? fmtElapsedSeconds(secs) : ''
}

// The output file grows with no event to ride: nothing notifies a browser
// that a file got longer, and the harness reports the shell only once, at the
// end. So the open panel re-reads on a timer — a refresh cadence, not a
// decision about state. Only ONE shell polls, and only while its panel is
// open, so a session with six running shells costs one request per tick.
let outputTimer = null
async function readOutput(taskId) {
  if (!taskId) return
  try {
    output.value = { ...output.value, [taskId]: await api.get(
      `/sessions/${props.live.traceId}/background/${taskId}/output`) }
  } catch (_) { /* a tick that fails is retried by the next one */ }
}
function toggle(taskId) {
  expanded.value = expanded.value === taskId ? null : taskId
  if (outputTimer) { clearInterval(outputTimer); outputTimer = null }
  if (!expanded.value) return
  readOutput(taskId)
  outputTimer = setInterval(() => readOutput(taskId), 3000)
}
onUnmounted(() => {
  if (tick) clearInterval(tick)
  if (outputTimer) clearInterval(outputTimer)
})
</script>

<template>
  <div
    v-if="running.length"
    class="my-2 rounded-[9px] border border-sky-200 bg-sky-50/40 overflow-hidden"
    data-background-shells
  >
    <button
      type="button"
      class="w-full flex items-center gap-2 px-3 py-[6px] text-left hover:bg-sky-50 focus-visible:outline-2 focus-visible:outline-blue-500"
      :aria-expanded="open"
      data-background-shells-toggle
      @click="open = !open"
    >
      <span class="w-2 text-center select-none text-[10.5px] text-sky-700">{{ open ? '▾' : '▸' }}</span>
      <span class="text-[12px] font-semibold text-sky-900">Background shells</span>
      <span
        class="text-[10px] bg-sky-100 border border-sky-200 text-sky-800 px-1 rounded"
        data-background-shells-count
      >{{ running.length }} running</span>
    </button>
    <div v-if="open" class="border-t border-sky-200">
      <div
        v-for="shell in running"
        :key="shell.task_id"
        class="border-b border-sky-100 last:border-b-0"
        data-background-shell-row
      >
        <div
          class="flex items-baseline gap-[9px] px-3 py-[5px] cursor-pointer hover:bg-sky-50"
          @click="toggle(shell.task_id)"
        >
          <span class="text-[12px] font-semibold text-slate-600 shrink-0">Shell</span>
          <span class="font-mono text-[11.5px] flex-1 min-w-0 truncate text-slate-700">{{ shell.description || shell.command }}</span>
          <span class="font-mono text-[10.5px] text-sky-600 shrink-0" data-background-shell-elapsed>{{ elapsed(shell) }}</span>
          <span class="text-[10.5px] text-slate-400 shrink-0 select-none w-3 text-center">{{ expanded === shell.task_id ? '▾' : '▸' }}</span>
        </div>
        <div v-if="expanded === shell.task_id" class="code-surface bg-slate-900 px-3 py-2">
          <div class="font-mono text-[11px] text-emerald-300 mb-1 break-words">{{ shell.command }}</div>
          <pre
            v-if="output[shell.task_id]?.available"
            class="live-term-body text-[12px] text-slate-100 whitespace-pre-wrap break-words font-mono leading-snug max-h-72 overflow-auto"
            data-background-shell-output
          >{{ output[shell.task_id].text }}</pre>
          <div
            v-else
            class="text-[11.5px] text-slate-400 font-mono"
            data-background-shell-output-missing
          >{{ output[shell.task_id] ? 'no readable output yet' : 'reading…' }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
