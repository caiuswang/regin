<script setup>
// Session vitals: the six numbers that answer "how big was this session?" —
// spans, duration, context peak, tokens, cost, turns.
//
// These used to live as ~12 text fragments inside the header's wrapping meta
// paragraph, where a long trace id or model name reflowed them unpredictably.
// Pulling them into a fixed cell grid gives each one a stable slot and a
// scannable label, and lets the context cell carry a gauge instead of a
// color-coded pill.
//
// Purely presentational: every input arrives as a prop, and a cell whose
// source value is missing renders an em-dash rather than disappearing (a
// vanishing cell would reflow the grid on every live poll).
import { computed } from 'vue'
import { fmtTokens, fmtCost } from '../utils/traceFormatters.js'

const props = defineProps({
  session: { type: Object, required: true },
  traceDuration: { type: Number, default: 0 },
  activeWorkMs: { type: Number, default: 0 },
  // /tool-rollup payload — supplies true spend + the tool-call count. Null
  // until the fetch lands.
  rollupData: { type: Object, default: null },
  turnCount: { type: [Number, null], default: null },
  agentCount: { type: Number, default: 0 },
})

const EMPTY = '—'

function fmtDuration(ms) {
  if (!ms) return EMPTY
  if (ms < 1000) return `${ms}ms`
  const units = [
    { value: Math.floor(ms / 86400000), label: 'd' },
    { value: Math.floor(ms / 3600000) % 24, label: 'h' },
    { value: Math.floor(ms / 60000) % 60, label: 'm' },
    { value: Math.floor(ms / 1000) % 60, label: 's' },
  ]
  const start = units.findIndex(u => u.value > 0)
  if (start === -1) return EMPTY
  let end = units.length - 1
  while (end > start && units[end].value === 0) end--
  return units.slice(start, end + 1).map(u => `${u.value}${u.label}`).join('')
}

const toolCalls = computed(() => {
  const rows = props.rollupData?.rollup
  if (!Array.isArray(rows)) return null
  return rows.reduce((n, r) => n + (r.calls || 0), 0)
})

// True spend = main-model bill + server-side sub-model (advisor) spend.
// `session_cost_usd` alone under-reports, so prefer total_spend_usd.
const spend = computed(() => {
  const d = props.rollupData
  if (!d) return null
  const usd = d.total_spend_usd ?? d.session_cost_usd
  return Number.isFinite(usd) ? usd : null
})

const totalTokens = computed(() => {
  const d = props.rollupData
  const t = d?.total_spend_tokens ?? d?.session_total_tokens
  return Number.isFinite(t) && t > 0 ? t : (props.session?.total_tokens ?? null)
})

const activePct = computed(() => {
  if (!props.traceDuration || !props.activeWorkMs) return null
  return Math.round((props.activeWorkMs / props.traceDuration) * 100)
})

// Live context peak (since the last /compact) — the same number the terminal
// shows, and the same one the header's ctx chip used to carry.
const ctxTokens = computed(() => props.session?.live_context_tokens
  ?? props.session?.peak_main_context_tokens
  ?? props.session?.peak_context_tokens
  ?? null)

const ctxPct = computed(() => {
  const p = props.session?.context_pct
  return Number.isFinite(p) ? p : null
})

// Gauge color tracks the same thresholds the old ctx pill used, so a session
// that read "amber" before still reads amber.
const ctxTone = computed(() => {
  const p = ctxPct.value
  if (p == null) return { text: 'text-slate-400', bar: 'bg-slate-300' }
  if (p >= 80) return { text: 'text-red-600', bar: 'bg-red-500' }
  if (p >= 50) return { text: 'text-amber-600', bar: 'bg-amber-500' }
  return { text: 'text-emerald-600', bar: 'bg-emerald-500' }
})

const spanCount = computed(() =>
  props.session?.span_count_total ?? props.session?.spans?.length ?? null)

const cells = computed(() => [
  {
    key: 'spans',
    label: 'Spans',
    value: spanCount.value != null ? String(spanCount.value) : EMPTY,
    sub: toolCalls.value != null ? `${toolCalls.value} tool calls` : '',
    tone: 'text-slate-900',
    title: 'spans captured for this session',
  },
  {
    key: 'duration',
    label: 'Duration',
    value: fmtDuration(Math.round(props.traceDuration)),
    sub: activePct.value != null ? `${activePct.value}% active` : '',
    tone: 'text-slate-900',
    title: 'wall-clock from first to last span — includes user-idle gaps between turns',
  },
  {
    key: 'context',
    label: 'Context peak',
    value: ctxPct.value != null ? `${ctxPct.value}%` : EMPTY,
    sub: ctxTokens.value != null && props.session?.context_window_tokens
      ? `${fmtTokens(ctxTokens.value)} / ${fmtTokens(props.session.context_window_tokens)}`
      : '',
    tone: ctxTone.value.text,
    gauge: ctxPct.value != null ? { pct: Math.min(100, ctxPct.value), bar: ctxTone.value.bar } : null,
    title: 'live context peak since the last /compact, as a share of the model window',
  },
  {
    key: 'tokens',
    label: 'Tokens',
    value: totalTokens.value != null ? fmtTokens(totalTokens.value) : EMPTY,
    sub: 'in + out + cache',
    tone: 'text-slate-900',
    title: 'every token billed for this session, including cache reads and writes',
  },
  {
    key: 'cost',
    label: 'Cost',
    value: spend.value != null ? fmtCost(spend.value) : EMPTY,
    sub: props.rollupData?.subagent_cost_usd ? 'incl. sub-model' : 'main model',
    tone: 'text-emerald-600',
    title: 'true spend: the main-model bill plus server-side sub-model (advisor) calls',
  },
  {
    key: 'turns',
    label: 'Turns',
    value: props.turnCount != null ? String(props.turnCount) : EMPTY,
    sub: props.agentCount ? `${props.agentCount} agent${props.agentCount === 1 ? '' : 's'}` : '',
    tone: 'text-slate-900',
    title: 'user prompts in this session',
  },
])
</script>

<template>
  <!-- Grid, not a flex row: six equal cells at 390px would each be ~60px and
       force the page into a horizontal scroll. It reflows to 2/3/6 columns
       instead, so the strip never widens `.content-scroll`. -->
  <dl
    class="mb-4 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-slate-200 bg-slate-200 sm:grid-cols-3 lg:grid-cols-6"
    data-testid="trace-vitals-strip"
  >
    <div
      v-for="c in cells"
      :key="c.key"
      class="flex min-w-0 flex-col gap-1.5 bg-slate-50 px-3.5 py-2.5"
      :title="c.title"
    >
      <dt class="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">{{ c.label }}</dt>
      <dd class="m-0 text-[19px] font-bold leading-none tabular-nums" :class="c.tone">{{ c.value }}</dd>
      <div v-if="c.gauge" class="h-[5px] overflow-hidden rounded-full bg-slate-200">
        <div class="h-full rounded-full" :class="c.gauge.bar" :style="{ width: c.gauge.pct + '%' }"></div>
      </div>
      <div
        v-if="c.sub"
        class="truncate font-mono text-[10.5px] leading-none text-slate-400"
      >{{ c.sub }}</div>
    </div>
  </dl>
</template>
