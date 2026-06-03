<script setup>
// Grafana-style mini-timeline overview, extracted from SessionTraceView: a
// horizontal strip with time-axis ticks, one colored bar per root span
// (positioned/sized by its time range), and faint turn-boundary hairlines.
// Clicking a bar selects that span.
//
// Stateless w.r.t. the data model: it takes the root nodes + the timing
// window + the current selection/turn highlight as props, and emits the
// clicked node back to the parent (which owns selection + tree expansion).
import { computed } from 'vue'
import { spanLabel } from '../utils/traceFormatters.js'

const props = defineProps({
  treeNodes: { type: Array, default: () => [] },
  selectedSpan: { type: Object, default: null },
  selectedTurnUuid: { type: [String, null], default: null },
  // Set of span_ids overlapping the selected turn (cross-highlight dimming).
  spanIdsInSelectedTurn: { type: Object, default: () => new Set() },
  turns: { type: Array, default: null },
  traceStart: { type: Number, default: 0 },
  traceEnd: { type: Number, default: 0 },
  traceDuration: { type: Number, default: 0 },
})

defineEmits(['select-node'])

// Local copy of SessionTraceView's duration formatter (its traceFormatters
// sibling behaves differently — see that file's note); kept in sync by hand.
function fmtDuration(ms) {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  const seconds = Math.floor(ms / 1000) % 60
  const minutes = Math.floor(ms / 60000) % 60
  const hours = Math.floor(ms / 3600000) % 24
  const days = Math.floor(ms / 86400000)
  const units = [
    { value: days, label: 'd' },
    { value: hours, label: 'h' },
    { value: minutes, label: 'm' },
    { value: seconds, label: 's' },
  ]
  const start = units.findIndex(u => u.value > 0)
  if (start === -1) return '-'
  let end = units.length - 1
  while (end > start && units[end].value === 0) end--
  return units.slice(start, end + 1).map(u => `${u.value}${u.label}`).join('')
}

// Distinct palette so each first-class span stands out regardless of name.
const spanPalette = [
  'bg-blue-500', 'bg-orange-500', 'bg-green-500', 'bg-purple-500',
  'bg-pink-500', 'bg-teal-500', 'bg-amber-500', 'bg-indigo-500',
  'bg-rose-500', 'bg-cyan-500', 'bg-lime-500', 'bg-fuchsia-500',
  'bg-emerald-500', 'bg-yellow-500', 'bg-sky-500', 'bg-violet-500',
]
function paletteColor(index) {
  return spanPalette[index % spanPalette.length]
}

function turnStartMs(turn) {
  return turn && turn.timestamp ? new Date(turn.timestamp).getTime() : null
}

function offsetPct(startTime) {
  const start = new Date(startTime).getTime()
  return ((start - props.traceStart) / props.traceDuration) * 100
}

function widthPct(startTime, endTime) {
  const start = new Date(startTime).getTime()
  const end = endTime ? new Date(endTime).getTime() : start
  const dur = Math.max(end - start, 50) // min 50ms visual width
  return (dur / props.traceDuration) * 100
}

// Grafana-style timeline ticks: 0/25/50/75/100% of the trace duration.
const timelineTicks = computed(() => {
  const total = props.traceDuration || 0
  return [0, 0.25, 0.5, 0.75, 1].map(p => ({
    pct: p * 100,
    label: fmtDuration(Math.round(total * p)),
  }))
})

// Vertical hairlines, one per turn timestamp.
const turnBoundaries = computed(() => {
  if (!props.turns || !props.traceStart || !props.traceDuration) return []
  return props.turns
    .map(t => turnStartMs(t))
    .filter(ms => ms != null && ms >= props.traceStart && ms <= props.traceEnd)
    .map(ms => ({ pct: ((ms - props.traceStart) / props.traceDuration) * 100 }))
})
</script>

<template>
  <div class="mb-4 rounded-xl border border-slate-200 bg-slate-50 px-4 pt-3 pb-3.5">
    <!-- Time axis -->
    <div class="relative h-4 w-full text-[10px] text-gray-500 font-mono">
      <div
        v-for="tick in timelineTicks"
        :key="'tl-' + tick.pct"
        class="absolute top-0"
        :style="{ left: tick.pct + '%', transform: tick.pct === 0 ? 'translateX(0)' : tick.pct === 100 ? 'translateX(-100%)' : 'translateX(-50%)' }"
      >{{ tick.label }}</div>
    </div>
    <!-- Bars + gridlines -->
    <div class="relative h-5 w-full bg-white rounded border border-gray-200 overflow-hidden">
      <!-- gridlines -->
      <div
        v-for="tick in timelineTicks"
        :key="'gl-' + tick.pct"
        class="absolute top-0 bottom-0 w-px bg-gray-200"
        :style="{ left: tick.pct + '%' }"
      ></div>
      <div
        v-for="(node, idx) in treeNodes"
        :key="node.data.span_id"
        data-testid="overview-strip-bar"
        class="absolute top-0.5 bottom-0.5 rounded-sm cursor-pointer transition-opacity hover:opacity-100 focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="[
          paletteColor(idx),
          selectedSpan && selectedSpan.span_id === node.data.span_id ? 'ring-2 ring-offset-1 ring-gray-800' : '',
          selectedTurnUuid && !spanIdsInSelectedTurn.has(node.data.span_id) ? 'opacity-20 hover:opacity-50' : 'opacity-90 hover:opacity-100',
        ]"
        :style="{ left: offsetPct(node.data.start_time) + '%', width: Math.max(widthPct(node.data.start_time, node.data.end_time), 0.2) + '%' }"
        :title="spanLabel(node.data) + ' — ' + fmtDuration(node.data.duration_ms)"
        @click="$emit('select-node', node)"
      ></div>
      <!-- Turn boundary markers — faint vertical lines so the user
           can see turn cadence at a glance without selecting. -->
      <div
        v-for="(b, i) in turnBoundaries"
        :key="'tb-' + i"
        class="absolute top-0 bottom-0 w-px bg-indigo-300/50 pointer-events-none"
        :style="{ left: b.pct + '%' }"
      ></div>
    </div>
  </div>
</template>
