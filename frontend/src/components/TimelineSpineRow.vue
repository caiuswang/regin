<script setup>
// One event in the Timeline "event spine": a vertical rail + colored node dot,
// the t+offset, the span label, an optional expand toggle, and a row of meta
// chips (duration bar, tokens, cost, status pill). `compact.*` spans collapse
// to a dashed-amber banner instead of a full row. Presentational only — it
// takes a raw span + display flags and emits select / toggle back to the spine.
import { computed } from 'vue'
import { spanLabel, fmtDuration, fmtTokens, fmtCost, mcpParts } from '../utils/traceFormatters.js'
import { barColor } from '../utils/spanColors.js'
import Button from './ui/Button.vue'

const props = defineProps({
  span: { type: Object, required: true },
  depth: { type: Number, default: 0 },
  selected: { type: Boolean, default: false },
  expanded: { type: Boolean, default: false },
  hasKids: { type: Boolean, default: false },
  childCount: { type: Number, default: 0 },
  // Longest root-span duration in the trace: normalizes the mini duration bar.
  rootMaxMs: { type: Number, default: 0 },
  // Trace start in epoch ms, so the row can render a t+m:ss offset.
  traceStart: { type: Number, default: 0 },
  showCost: { type: Boolean, default: true },
})
const emit = defineEmits(['select', 'toggle'])

const isCompact = computed(() => (props.span.name || '').startsWith('compact.'))
const dotColor = computed(() => barColor(props.span.name || ''))
const label = computed(() => spanLabel(props.span))
const durTxt = computed(() => fmtDuration(props.span.duration_ms) || '—')

const relStartTxt = computed(() => {
  const t = props.span.start_time ? new Date(props.span.start_time).getTime() : NaN
  if (!Number.isFinite(t) || !props.traceStart) return '0:00'
  const secs = Math.max(0, Math.floor((t - props.traceStart) / 1000))
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`
})

const tokTotal = computed(() => (props.span.input_tokens || 0) + (props.span.output_tokens || 0))
const tokTxt = computed(() => fmtTokens(tokTotal.value))
const showCostChip = computed(() => props.showCost && props.span.cost_usd > 0)
const costTxt = computed(() => fmtCost(props.span.cost_usd))

const barWidth = computed(() => {
  const max = props.rootMaxMs || 1
  const pct = Math.max(Math.min(((props.span.duration_ms || 0) / max) * 100, 100), 5)
  return `${pct}%`
})
const indent = computed(() => `${Math.min(props.depth, 5) * 24}px`)

// Status/kind badge shown after the meta chips — mirrors the pill logic from
// the source design, keyed off regin's real span names/attributes.
const PILL = {
  red: 'text-red-700 bg-red-50 border border-red-200',
  blue: 'text-blue-700 bg-blue-50 border border-blue-200',
  cyan: 'text-cyan-700 bg-cyan-50 border border-cyan-200',
  slate: 'text-slate-600 bg-slate-50 border border-slate-200',
}
const pill = computed(() => {
  const n = props.span.name || ''
  const a = props.span.attributes || {}
  if (n === 'tool.failure') return { text: 'failed', cls: PILL.red }
  if (a.rejected || a.denied) return { text: 'blocked', cls: PILL.red }
  if (n === 'rule.check' && (a.violating_rule_count || 0) > 0) return { text: 'violations', cls: PILL.red }
  if (n === 'rewind') return { text: 'rewind', cls: PILL.red }
  if (mcpParts(n)) return { text: 'MCP', cls: PILL.cyan }
  if (n === 'tool.AskUserQuestion') return { text: 'question', cls: PILL.blue }
  if (n === 'subagent.start' || n === 'tool.Agent') return { text: 'subagent', cls: PILL.slate }
  return null
})
</script>

<template>
  <!-- compact.* banner -->
  <div
    v-if="isCompact"
    :data-span-id="span.span_id"
    data-testid="spine-row"
    role="button"
    tabindex="0"
    class="flex cursor-pointer items-center gap-2.5 my-1.5 ml-[30px] rounded-lg border border-dashed border-amber-300 bg-amber-50 px-3 py-1.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-amber-500"
    :class="selected ? 'ring-2 ring-amber-400' : 'hover:bg-amber-100/70'"
    @click="emit('select')"
    @keydown.enter.prevent="emit('select')"
    @keydown.space.prevent="emit('select')"
  >
    <span class="shrink-0 font-mono text-[10.5px] text-amber-700">t+{{ relStartTxt }}</span>
    <span class="h-2 w-2 shrink-0 rounded-full bg-amber-500"></span>
    <span class="truncate text-[12.5px] font-medium text-amber-800">{{ label }}</span>
  </div>

  <!-- full event row -->
  <div
    v-else
    :data-span-id="span.span_id"
    data-testid="spine-row"
    role="button"
    tabindex="0"
    class="relative flex cursor-pointer gap-3 rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500"
    :class="selected ? 'bg-blue-50 shadow-[inset_2px_0_0_var(--color-blue-600)]' : 'hover:bg-slate-50'"
    :style="{ marginLeft: indent }"
    @click="emit('select')"
    @keydown.enter.prevent="emit('select')"
    @keydown.space.prevent="emit('select')"
  >
    <div class="relative flex w-4 shrink-0 justify-center">
      <div class="absolute inset-y-0 w-0.5 bg-slate-200"></div>
      <div
        class="relative z-[1] mt-3 h-3 w-3 rounded-full border-2 border-white ring-1 ring-slate-300"
        :class="dotColor"
      ></div>
    </div>

    <div class="min-w-0 flex-1 py-2.5">
      <div class="flex items-baseline gap-2.5">
        <!-- min-width, not a fixed `w-10`: a long-session offset (`t+126:08`)
             is wider than 40px, and a fixed column let it spill under the
             label so the two read as one run-on string. Short offsets still
             align because the minimum holds the column. -->
        <span class="min-w-[3.5rem] shrink-0 font-mono text-[10.5px] tabular-nums text-slate-400">t+{{ relStartTxt }}</span>
        <span class="min-w-0 truncate text-[13.5px] font-semibold text-slate-800">{{ label }}</span>
        <Button
          v-if="hasKids"
          variant="ghost"
          size="sm"
          data-testid="spine-toggle"
          :title="expanded ? 'Collapse' : 'Expand'"
          :aria-label="expanded ? 'Collapse children' : `Expand ${childCount} children`"
          class="h-auto shrink-0 gap-0.5 rounded px-1 py-0 font-mono text-[10px] text-slate-400 hover:text-slate-600"
          @click.stop="emit('toggle')"
        >
          <svg
            class="h-2.5 w-2.5 transition-transform"
            :class="expanded ? 'rotate-90' : ''"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
          ><polyline points="9 18 15 12 9 6" /></svg>
          <span v-if="!expanded && childCount">{{ childCount }}</span>
        </Button>
      </div>

      <div class="mt-1.5 flex flex-wrap items-center gap-1.5 pl-[51px]">
        <span class="font-mono text-[10.5px] text-slate-400">{{ span.name }}</span>
        <span class="inline-flex items-center gap-1.5 rounded-md bg-slate-100 px-1.5 py-0.5 text-[10.5px] text-slate-500">
          <span class="relative h-1 w-10 overflow-hidden rounded-sm bg-slate-200">
            <span class="absolute inset-y-0 left-0 rounded-sm" :class="dotColor" :style="{ width: barWidth }"></span>
          </span>
          <span class="font-mono">{{ durTxt }}</span>
        </span>
        <span
          v-if="tokTotal > 0"
          class="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-500"
        >{{ tokTxt }} tok</span>
        <span
          v-if="showCostChip"
          class="rounded-md border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 font-mono text-[10.5px] text-emerald-700"
        >{{ costTxt }}</span>
        <span
          v-if="pill"
          class="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
          :class="pill.cls"
        >{{ pill.text }}</span>
      </div>
    </div>
  </div>
</template>
