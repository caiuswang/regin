<script setup>
// Grafana-style mini-timeline overview, extracted from SessionTraceView: a
// horizontal strip with time-axis ticks, one colored bar per root span
// (positioned/sized by its time range), and faint turn-boundary hairlines.
// Clicking a bar selects that span.
//
// Stateless w.r.t. the data model: it takes the root nodes + the timing
// window + the current selection/turn highlight as props, and emits the
// clicked node back to the parent (which owns selection + tree expansion).
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { spanLabel } from '../utils/traceFormatters.js'
import Button from './ui/Button.vue'

const props = defineProps({
  treeNodes: { type: Array, default: () => [] },
  selectedSpan: { type: Object, default: null },
  selectedTurnUuid: { type: [String, null], default: null },
  // Set of span_ids overlapping the selected turn (cross-highlight dimming).
  spanIdsInSelectedTurn: { type: Object, default: () => new Set() },
  turns: { type: Array, default: null },
  // Workflow runs have no user-prompt turns: their turn_usage rows are the
  // subagents' API responses, so the turns fallback below would flag every
  // agent turn as a prompt.
  isWorkflow: { type: Boolean, default: false },
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

// Bars are colored by what the span DID, not by its position in the list. The
// old rotating palette made adjacent bars different colors for no reason, which
// meant the strip could never carry a legend — the one thing that turns a row
// of colored blocks into something readable at a glance.
const KINDS = [
  { key: 'prompt', label: 'Prompt', bar: 'bg-purple-500', dot: 'bg-purple-500' },
  { key: 'read', label: 'Reads', bar: 'bg-blue-500', dot: 'bg-blue-500' },
  { key: 'write', label: 'Writes', bar: 'bg-orange-500', dot: 'bg-orange-500' },
  { key: 'shell', label: 'Shell', bar: 'bg-teal-500', dot: 'bg-teal-500' },
  { key: 'check', label: 'Checks', bar: 'bg-red-500', dot: 'bg-red-500' },
  { key: 'agent', label: 'Subagent', bar: 'bg-violet-500', dot: 'bg-violet-500' },
  { key: 'model', label: 'Model', bar: 'bg-emerald-500', dot: 'bg-emerald-500' },
  { key: 'system', label: 'System', bar: 'bg-slate-300', dot: 'bg-slate-300' },
  { key: 'other', label: 'Other', bar: 'bg-slate-400', dot: 'bg-slate-400' },
]
const KIND_BAR = Object.fromEntries(KINDS.map(k => [k.key, k.bar]))

// Span names come from lib/trace's builders; these prefixes are the stable part
// of that vocabulary. Anything unrecognised falls to 'other' rather than being
// forced into a bucket, so a new span family shows up as visibly unclassified
// instead of silently miscolored.
const SYSTEM_PREFIXES = ['harness.', 'cwd.', 'instructions.', 'session.', 'permission.', 'compact.']

function spanKind(name) {
  if (!name) return 'other'
  if (name === 'prompt') return 'prompt'
  if (name === 'rule.check') return 'check'
  if (name === 'file.edit' || name === 'plan.edit') return 'write'
  if (name.startsWith('subagent') || name.startsWith('workflow.agent')) return 'agent'
  if (name.startsWith('assistant')) return 'model'
  if (SYSTEM_PREFIXES.some(p => name.startsWith(p))) return 'system'
  if (name === 'tool.Bash') return 'shell'
  if (name === 'tool.Write' || name === 'tool.Edit' || name === 'tool.MultiEdit') return 'write'
  if (name === 'tool.Agent' || name === 'tool.Task') return 'agent'
  if (name.startsWith('skill.') || name.startsWith('memory.') || name.startsWith('tool.')) return 'read'
  return 'other'
}

// A very long session can hold thousands of spans; past a few hundred bars the
// strip is solid color anyway and the DOM cost stops buying anything. Newest
// wins, because the recent tail is what a live watcher is looking at.
const MAX_BARS = 500

// Root spans, NOT the full span list. The trace view loads shallowly — only
// roots plus whatever subtrees the reader has expanded — so plotting
// `allSpans` drew a near-empty strip that got sparser the less you had
// explored. Roots are the set that is always complete for the loaded window,
// which is what a session-level overview has to be honest about.
//
// Prompt roots stay in: a prompt span covers its whole turn, and that band is
// the turn extent. The flags above mark where each one starts.
// Whole nodes, not bare `data`: the parent's click handler reads `key` and
// `leaf` off the node to expand the timeline subtree, so a `{ data }` wrapper
// would silently stop expanding anything and refetch children for leaves.
const bars = computed(() => props.treeNodes
  .filter(n => n?.data?.start_time)
  .slice(-MAX_BARS))

// Only the kinds actually present get a legend swatch — a key listing colors
// that appear nowhere on the bar is noise.
const legend = computed(() => {
  const present = new Set(bars.value.map(n => spanKind(n.data.name)))
  return KINDS.filter(k => present.has(k.key))
})

function barColor(span) {
  return KIND_BAR[spanKind(span?.name)] || 'bg-slate-400'
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
    // fmtDuration(0) is "-", which reads as "no data" at the origin of an axis.
    label: p === 0 ? '0' : fmtDuration(Math.round(total * p)),
  }))
})

// One entry per turn: a hairline on the bar plus a numbered, clickable flag
// above it. The bare hairlines were unreadable — a reader could see *that* the
// cadence changed but not which turn a burst belonged to, which is the only
// reason to look at turn boundaries in the first place.
//
// Driven off the PROMPT SPANS, not the turn_usage rows. A "turn" here means
// "the user said something", which is what the reader is orienting by; the
// turn_usage table is a per-API-response billing record that is often still
// null on a live session, so keying off it left the strip with no flags at all
// on exactly the sessions people watch most.
const turnBoundaries = computed(() => {
  if (!props.traceStart || !props.traceDuration) return []
  const prompts = props.treeNodes
    .filter(n => n?.data?.name === 'prompt' && n.data.start_time)
    .map(n => ({ ms: new Date(n.data.start_time).getTime(), node: n }))
  const source = prompts.length
    ? prompts
    : props.isWorkflow
      ? []
      : (props.turns || []).map(t => ({ ms: turnStartMs(t), node: null }))
  return source
    .filter(t => t.ms != null && t.ms >= props.traceStart && t.ms <= props.traceEnd)
    .sort((a, b) => a.ms - b.ms)
    .map((t, i) => ({
      num: i + 1,
      node: t.node,
      pct: ((t.ms - props.traceStart) / props.traceDuration) * 100,
    }))
})

// Percent positioning alone stacked the pills on top of each other whenever
// two prompts landed close in time, and the one underneath lost the very digit
// it exists to show. De-colliding needs real pixels, so measure the row.
const flagRowRef = ref(null)
const flagRowWidth = ref(0)
let flagRowObserver = null

// Watched, not measured once on mount: the flag row sits behind a `v-if` on
// `turnBoundaries`, so on a session whose first prompt lands after this
// component mounts (any live session opened early) a mount-time measure finds
// no element, and the width would stay 0 forever — silently reverting every
// flag to the percent positioning that stacks them.
watch(flagRowRef, (el) => {
  flagRowObserver?.disconnect()
  flagRowObserver = null
  if (!el || typeof ResizeObserver === 'undefined') {
    flagRowWidth.value = 0
    return
  }
  flagRowWidth.value = el.clientWidth
  flagRowObserver = new ResizeObserver(entries => {
    flagRowWidth.value = entries[0].contentRect.width
  })
  flagRowObserver.observe(el)
}, { immediate: true })
onBeforeUnmount(() => {
  flagRowObserver?.disconnect()
  flagRowObserver = null
})

// px-1.5 either side + the glyph + ~6px per tabular digit.
function flagWidth(num) {
  return 22 + 6 * String(num).length
}
const FLAG_GAP = 3

// The hairlines on the bar below still mark EVERY turn — only the pills are
// thinned and nudged, because they are the part that has to stay legible.
const placedFlags = computed(() => {
  const width = flagRowWidth.value
  const all = turnBoundaries.value
  if (!width || !all.length) return all.map(b => ({ ...b, left: null, w: flagWidth(b.num) }))

  // If even a tight packing cannot fit, drop to an evenly spaced subset (the
  // last turn always survives). Letting all of them through just piles the
  // surplus against the right edge, which is the bug in a different costume.
  const needed = all.reduce((sum, b) => sum + flagWidth(b.num) + FLAG_GAP, 0)
  const stride = needed > width ? Math.ceil(needed / width) : 1
  const kept = stride === 1
    ? all
    : all.filter((b, i) => i % stride === 0 || i === all.length - 1)

  // Left-to-right sweep: start each pill at its true time position, then push
  // it right only as far as clearing its predecessor requires.
  let cursor = 0
  const placed = kept.map(b => {
    const w = flagWidth(b.num)
    const left = Math.max(cursor, (b.pct / 100) * width - w / 2)
    cursor = left + w + FLAG_GAP
    return { ...b, left, w }
  })

  // A sweep that ran off the right edge gets pulled back in from the tail.
  if (cursor - FLAG_GAP > width) {
    let limit = width
    for (let i = placed.length - 1; i >= 0; i--) {
      placed[i].left = Math.min(placed[i].left, limit - placed[i].w)
      limit = placed[i].left - FLAG_GAP
    }
  }
  return placed.map(f => ({ ...f, left: Math.max(0, f.left) }))
})
</script>

<template>
  <div class="mb-4 rounded-xl border border-slate-200 bg-slate-50 px-4 pt-2.5 pb-3.5">
    <div class="mb-2 flex items-baseline justify-between gap-3">
      <h3 class="m-0 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">Activity</h3>
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-400">
        <span v-for="l in legend" :key="l.key" class="inline-flex items-center gap-1.5">
          <span class="h-[7px] w-[7px] rounded-sm" :class="l.dot"></span>{{ l.label }}
        </span>
      </div>
    </div>
    <!-- Numbered turn flags. Kept in their own row above the bar so a dense
         run of turns crowds the flags, not the spans. -->
    <div v-if="turnBoundaries.length" ref="flagRowRef" class="relative mb-1 h-[17px]">
      <Button
        v-for="b in placedFlags"
        :key="'tf-' + b.num"
        variant="ghost"
        size="sm"
        data-testid="turn-flag"
        class="absolute top-0 h-[17px] gap-1 rounded-full border border-purple-200 bg-white px-1.5 text-[9.5px] font-medium tabular-nums text-purple-700 hover:border-purple-400 hover:bg-purple-50"
        :style="b.left != null
          ? { left: b.left + 'px' }
          : { left: b.pct + '%', transform: b.pct === 0 ? 'translateX(0)' : 'translateX(-50%)' }"
        :title="`user prompt · start of turn ${b.num}`"
        @click="b.node && $emit('select-node', b.node)"
      >⚐{{ b.num }}</Button>
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
        v-for="node in bars"
        :key="node.data.span_id"
        data-testid="overview-strip-bar"
        class="absolute top-0.5 bottom-0.5 rounded-sm cursor-pointer transition-opacity hover:opacity-100 focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="[
          barColor(node.data),
          selectedSpan && selectedSpan.span_id === node.data.span_id ? 'ring-2 ring-offset-1 ring-gray-800' : '',
          selectedTurnUuid && !spanIdsInSelectedTurn.has(node.data.span_id) ? 'opacity-20 hover:opacity-50' : 'opacity-90 hover:opacity-100',
        ]"
        :style="{ left: offsetPct(node.data.start_time) + '%', width: Math.max(widthPct(node.data.start_time, node.data.end_time), 0.45) + '%' }"
        :title="spanLabel(node.data) + ' — ' + fmtDuration(node.data.duration_ms)"
        @click="$emit('select-node', node)"
      ></div>
      <!-- Turn boundary markers — faint vertical lines so the user
           can see turn cadence at a glance without selecting. -->
      <div
        v-for="b in turnBoundaries"
        :key="'tb-' + b.num"
        class="absolute top-0 bottom-0 w-px bg-purple-400/70 pointer-events-none"
        :style="{ left: b.pct + '%' }"
      ></div>
    </div>
    <!-- Time axis sits UNDER the bar: the flags above already claim that edge,
         and a reader scanning a bar drops to the axis to place it in time. -->
    <div class="relative mt-1 h-3.5 w-full font-mono text-[9.5px] text-slate-400">
      <div
        v-for="tick in timelineTicks"
        :key="'tl-' + tick.pct"
        class="absolute top-0"
        :style="{ left: tick.pct + '%', transform: tick.pct === 0 ? 'translateX(0)' : tick.pct === 100 ? 'translateX(-100%)' : 'translateX(-50%)' }"
      >{{ tick.label }}</div>
    </div>
  </div>
</template>
