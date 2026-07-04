<script setup>
// The "report-card header": four KPIs plus a per-axis verdict-distribution
// strip. Everything here is derived — the band owns no state. The KPIs come
// from the pareto `summary` (already cost-aware) and the off-frontier
// `frontier` counts; the distribution bars are counted from the visible rows
// so they track the same data the list shows.
import { computed } from 'vue'
import { toneOf, TONE_META } from '../../constants/gradeVerdicts'

const props = defineProps({
  summary: { type: Object, default: null },
  rows: { type: Array, default: () => [] },
  // { cheaplyWrong, expensivelyRight } — derived from pareto points upstream.
  frontier: { type: Object, default: () => ({ cheaplyWrong: 0, expensivelyRight: 0 }) },
})

const sessions = computed(() => props.summary?.sessions ?? props.rows.length)
const satisfied = computed(() => props.summary?.satisfied ?? 0)
const satisfiedPct = computed(() =>
  sessions.value ? Math.round((satisfied.value / sessions.value) * 100) : 0)
const flagged = computed(() =>
  (props.frontier.cheaplyWrong || 0) + (props.frontier.expensivelyRight || 0))

function money(v) {
  return v == null ? '—' : `$${Number(v).toFixed(2)}`
}

const ICONS = {
  chart: 'M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z',
  check: 'M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  dollar: 'M12 6v12m-3-2.818.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-.725 0-1.45-.22-2.003-.659-1.106-.879-1.106-2.303 0-3.182s2.9-.879 4.006 0l.415.33M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  scale: 'M12 3v17.25m0 0c-1.472 0-2.882.265-4.185.75M12 20.25c1.472 0 2.882.265 4.185.75M18.75 4.97A48.416 48.416 0 0 0 12 4.5c-2.291 0-4.545.16-6.75.47m13.5 0c1.01.143 2.01.317 3 .52m-3-.52 2.62 10.726c.122.499-.106 1.028-.589 1.202a5.988 5.988 0 0 1-2.031.352 5.988 5.988 0 0 1-2.031-.352c-.483-.174-.711-.703-.59-1.202L18.75 4.971Zm-16.5.52c.99-.203 1.99-.377 3-.52m0 0 2.62 10.726c.122.499-.106 1.028-.589 1.202a5.989 5.989 0 0 1-2.031.352 5.989 5.989 0 0 1-2.031-.352c-.483-.174-.711-.703-.59-1.202L5.25 4.971Z',
}

const kpis = computed(() => [
  {
    key: 'sessions', label: 'Sessions graded', value: sessions.value,
    sub: 'latest verdict per session', icon: ICONS.chart, tint: 'text-slate-500 bg-slate-100',
  },
  {
    key: 'satisfied', label: 'Satisfied', value: satisfied.value,
    sub: `${satisfiedPct.value}% of graded`, icon: ICONS.check, tint: 'text-emerald-600 bg-emerald-100',
    bar: satisfiedPct.value,
  },
  {
    key: 'cost', label: 'Cost / correct outcome',
    value: money(props.summary?.cost_per_correct_outcome),
    sub: props.summary ? `${money(props.summary.total_cost_usd)} total` : '—',
    icon: ICONS.dollar, tint: 'text-amber-600 bg-amber-100',
  },
  {
    key: 'frontier', label: 'Off-frontier', value: flagged.value,
    sub: `${props.frontier.cheaplyWrong || 0} cheaply wrong · ${props.frontier.expensivelyRight || 0} costly`,
    icon: ICONS.scale, tint: 'text-rose-600 bg-rose-100',
  },
])

const TONE_ORDER = ['pass', 'warn', 'fail', 'unknown']

// One distribution row per axis actually present in the data, each a stacked
// pass/warn/fail/ungraded bar. Counted from the visible rows so the strip and
// the list never disagree.
const distributions = computed(() => {
  const byAxis = new Map()
  for (const row of props.rows) {
    for (const axis of Object.keys(row.axes || {})) {
      const counts = byAxis.get(axis) || { pass: 0, warn: 0, fail: 0, unknown: 0 }
      counts[toneOf(row.axes[axis]?.verdict)] += 1
      byAxis.set(axis, counts)
    }
  }
  return [...byAxis.entries()].map(([axis, counts]) => {
    const total = TONE_ORDER.reduce((n, t) => n + counts[t], 0) || 1
    return {
      axis,
      total: total,
      segments: TONE_ORDER
        .filter(t => counts[t] > 0)
        .map(t => ({ tone: t, count: counts[t], pct: (counts[t] / total) * 100 })),
    }
  })
})
</script>

<template>
  <section class="space-y-4">
    <!-- KPI cards -->
    <div class="grid gap-3 grid-cols-2 lg:grid-cols-4">
      <div
        v-for="k in kpis"
        :key="k.key"
        class="rounded-xl border border-slate-200 bg-white p-4 shadow-[0_1px_2px_rgba(15,23,42,0.03)]"
      >
        <div class="flex items-start justify-between gap-2">
          <div class="text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-slate-400">
            {{ k.label }}
          </div>
          <span :class="['inline-flex h-7 w-7 items-center justify-center rounded-lg shrink-0', k.tint]">
            <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" :d="k.icon" />
            </svg>
          </span>
        </div>
        <div class="mt-2 text-2xl font-bold leading-none text-slate-900 tabular-nums">{{ k.value }}</div>
        <!-- Satisfied gets a rate bar; others just a caption. -->
        <div v-if="k.bar != null" class="mt-2.5">
          <div class="h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
            <div class="h-full rounded-full bg-emerald-400 transition-[width] duration-500" :style="{ width: k.bar + '%' }"></div>
          </div>
        </div>
        <div class="mt-1.5 text-xs text-slate-500">{{ k.sub }}</div>
      </div>
    </div>

    <!-- Verdict distribution per axis -->
    <div
      v-if="distributions.length"
      class="rounded-xl border border-slate-200 bg-white p-4 shadow-[0_1px_2px_rgba(15,23,42,0.03)]"
    >
      <div class="flex items-center justify-between gap-3 mb-3">
        <h2 class="text-sm font-semibold text-slate-800">Verdict distribution</h2>
        <div class="flex flex-wrap items-center gap-3">
          <span v-for="t in TONE_ORDER" :key="t" class="flex items-center gap-1.5 text-[11px] text-slate-500">
            <span :class="['h-2 w-2 rounded-full', TONE_META[t].dot]" aria-hidden="true"></span>
            {{ TONE_META[t].label }}
          </span>
        </div>
      </div>
      <div class="space-y-2.5">
        <div v-for="d in distributions" :key="d.axis" class="flex items-center gap-3">
          <div class="w-24 shrink-0 text-xs font-medium capitalize text-slate-600">{{ d.axis }}</div>
          <div class="flex h-2.5 flex-1 overflow-hidden rounded-full bg-slate-100">
            <div
              v-for="s in d.segments"
              :key="s.tone"
              :class="['h-full first:rounded-l-full last:rounded-r-full', TONE_META[s.tone].bar]"
              :style="{ width: s.pct + '%' }"
              :title="`${TONE_META[s.tone].label}: ${s.count}`"
            ></div>
          </div>
          <div class="w-10 shrink-0 text-right text-xs tabular-nums text-slate-400">{{ d.total }}</div>
        </div>
      </div>
    </div>
  </section>
</template>
