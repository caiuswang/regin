<script setup>
// The redesigned top of the span-detail rail: an uppercase section label +
// status dot, the human span label with its raw name, and a 2×2 grid of
// bordered metric cards (Duration / Started / Tokens / Cost). regin-specific
// fields the design's mock omits (source, kind, status, span id, wall-clock
// window) are preserved as a demoted meta block below the hero grid so nothing
// is lost. Presentational; the per-kind detail blocks stay in SpanDetailPanel.
import { computed } from 'vue'
import { spanLabel, fmtTokens, fmtCost, fmtDuration, mcpParts } from '../utils/traceFormatters.js'
import { barColor } from '../utils/spanColors.js'

const props = defineProps({
  span: { type: Object, required: true },
  // Trace start in epoch ms, for the t+m:ss "Started" readout.
  traceStart: { type: Number, default: 0 },
})

const label = computed(() => spanLabel(props.span))
const dotColor = computed(() => barColor(props.span.name || ''))
const durTxt = computed(() => fmtDuration(props.span.duration_ms) || '—')
const startedTxt = computed(() => {
  const t = props.span.start_time ? new Date(props.span.start_time).getTime() : NaN
  if (!Number.isFinite(t) || !props.traceStart) return '—'
  const secs = Math.max(0, Math.floor((t - props.traceStart) / 1000))
  return `t+${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`
})
const inTxt = computed(() => (props.span.input_tokens ? fmtTokens(props.span.input_tokens) : '—'))
const outTxt = computed(() => (props.span.output_tokens ? fmtTokens(props.span.output_tokens) : '—'))
const costTxt = computed(() => (props.span.cost_usd ? fmtCost(props.span.cost_usd) : '$0'))

function fmtClock(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString() + '.' + String(d.getMilliseconds()).padStart(3, '0')
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between border-b border-slate-100 pb-2.5">
      <h2 class="text-[10px] font-bold uppercase tracking-widest text-slate-400">Span details</h2>
      <span class="h-2 w-2 rounded-full" :class="dotColor" aria-hidden="true"></span>
    </div>

    <div class="mt-3 break-words text-[15px] font-semibold leading-snug text-slate-900">{{ label }}</div>
    <div class="mt-0.5 flex flex-wrap items-center gap-1.5 font-mono text-[11px] text-slate-400">
      <span
        v-if="mcpParts(span.name)"
        class="rounded bg-cyan-100 px-1 py-px text-[9px] font-semibold uppercase tracking-wider text-cyan-800"
      >MCP</span>
      <span class="break-all">{{ span.name }}</span>
    </div>

    <div class="mt-3.5 grid grid-cols-2 gap-2">
      <div class="rounded-lg border border-slate-100 px-2.5 py-2">
        <div class="text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">Duration</div>
        <div class="mt-0.5 font-mono text-[15px] font-semibold text-slate-900">{{ durTxt }}</div>
      </div>
      <div class="rounded-lg border border-slate-100 px-2.5 py-2">
        <div class="text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">Started</div>
        <div class="mt-0.5 font-mono text-[15px] font-semibold text-slate-900">{{ startedTxt }}</div>
      </div>
      <div class="rounded-lg border border-slate-100 px-2.5 py-2">
        <div class="text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">Tokens (in / out)</div>
        <div class="mt-0.5 font-mono text-[14px] font-semibold text-slate-900">{{ inTxt }} / {{ outTxt }}</div>
      </div>
      <div class="rounded-lg border border-slate-100 px-2.5 py-2">
        <div class="text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">Cost</div>
        <div class="mt-0.5 font-mono text-[14px] font-semibold text-emerald-700">{{ costTxt }}</div>
      </div>
    </div>

    <dl class="mt-3 space-y-1 text-[11px]">
      <div class="flex gap-2">
        <dt class="w-16 shrink-0 text-slate-400">Source</dt>
        <dd class="min-w-0 font-mono text-slate-600">{{ span.source || 'hook' }} · {{ span.kind || '—' }} · {{ span.status_code || 'OK' }}</dd>
      </div>
      <div class="flex gap-2">
        <dt class="w-16 shrink-0 text-slate-400">Span ID</dt>
        <dd class="min-w-0 break-all font-mono text-slate-600">{{ span.span_id }}</dd>
      </div>
      <div class="flex gap-2">
        <dt class="w-16 shrink-0 text-slate-400">Window</dt>
        <dd class="min-w-0 break-all font-mono text-slate-600">{{ fmtClock(span.start_time) }} → {{ fmtClock(span.end_time || span.start_time) }}</dd>
      </div>
    </dl>
  </div>
</template>
