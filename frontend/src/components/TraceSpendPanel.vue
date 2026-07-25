<script setup>
// "Overview · token spend" — the disclosure that holds the session's two cost
// readings: the per-tool leaderboard and the full bill by billing category.
//
// Collapsed by default. The vitals strip above already answers "what did this
// cost?"; this panel answers "where did it go?", which is a deliberate second
// question, not something that should occupy header space on every load.
import { ref, computed } from 'vue'
import { fmtTokens, fmtCost } from '../utils/traceFormatters.js'
import TraceSpendLeaderboard from './TraceSpendLeaderboard.vue'
import Button from './ui/Button.vue'
import Icon from './ui/Icon.vue'

const props = defineProps({
  rollupData: { type: Object, default: null },
})

defineEmits(['jump-span'])

const open = ref(false)

// The bill is ordered by cost, not by token count — cache replay usually
// dominates both, but output can outrank cache write on dollars while sitting
// two orders of magnitude below it on tokens.
const BILL_ROWS = [
  { key: 'cache_read', label: 'Context replay', sub: 'cache read · replayed every turn',
    cost: 'cache_read_cost_usd', tokens: 'session_cache_read_tokens', accent: 'bg-amber-500' },
  { key: 'cache_write', label: 'Cache write', sub: 'cache creation',
    cost: 'cache_write_cost_usd', tokens: 'session_cache_creation_tokens', accent: 'bg-blue-500' },
  { key: 'output', label: 'Model output', sub: 'generated tokens',
    cost: 'output_cost_usd', tokens: 'session_output_tokens', accent: 'bg-emerald-500' },
  { key: 'input', label: 'Base input', sub: 'prompt tokens',
    cost: 'input_cost_usd', tokens: 'session_input_tokens', accent: 'bg-slate-500' },
  // Server-side sub-model (advisor) spend. `session_cost_usd` excludes it, so
  // without this row the listed items would not add up to the total.
  { key: 'subagent', label: 'Sub-model calls', sub: 'server-side advisor · excluded from session cost',
    cost: 'subagent_cost_usd', tokens: 'subagent_tokens', accent: 'bg-violet-500' },
]

const bill = computed(() => {
  const d = props.rollupData
  if (!d) return null
  const totalCost = d.total_spend_usd ?? d.session_cost_usd ?? 0
  const denom = totalCost || 1
  const rows = BILL_ROWS
    .map(r => ({ ...r, costUsd: d[r.cost] || 0, tokenCount: d[r.tokens] || 0 }))
    .filter(r => r.costUsd > 0 || r.tokenCount > 0)
    .sort((a, b) => b.costUsd - a.costUsd)
    .map(r => ({ ...r, pct: Math.round((r.costUsd / denom) * 100) + '%' }))
  if (!rows.length) return null
  return {
    rows,
    totalCost: fmtCost(totalCost),
    totalTokens: fmtTokens(d.total_spend_tokens ?? d.session_total_tokens ?? 0),
  }
})
</script>

<template>
  <div class="mb-4">
    <Button
      variant="ghost"
      size="sm"
      class="h-auto gap-1.5 px-0 text-xs font-semibold text-slate-600 hover:bg-transparent hover:text-slate-900"
      :aria-expanded="open"
      data-testid="trace-spend-toggle"
      @click="open = !open"
    >
      <Icon :name="open ? 'chevron-down' : 'chevron-right'" :size="14" />
      Overview
      <span class="font-normal text-slate-400">· token spend</span>
    </Button>

    <!-- `[&>*]:shrink-0` is load-bearing: as flex items the sections default to
         shrink:1, so they compressed to the max-h instead of overflowing it.
         Their own overflow-hidden (there for the rounded corners) then clipped
         the squeezed-out rows while the scroller had nothing to scroll. -->
    <div
      v-if="open"
      class="mt-2.5 flex max-h-[42vh] flex-col gap-3.5 overflow-y-auto pr-0.5 [&>*]:shrink-0"
      data-testid="trace-spend-scroll"
    >
      <TraceSpendLeaderboard :rollup-data="rollupData" @jump-span="$emit('jump-span', $event)" />

      <section
        v-if="bill"
        class="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.03)]"
      >
        <header class="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-4 pb-3 pt-3.5">
          <h4 class="m-0 text-[10px] font-semibold uppercase tracking-[0.09em] text-slate-400">Full session bill</h4>
          <p class="m-0 text-[11.5px] text-slate-400">
            where the <span class="font-semibold text-slate-900">{{ bill.totalTokens }}</span> tokens went
          </p>
        </header>
        <div
          class="grid items-center gap-x-2 border-b border-slate-200 px-3 pb-2 text-[9.5px] font-semibold uppercase tracking-[0.06em] text-slate-400 [grid-template-columns:20px_1fr_74px_64px] sm:gap-x-3 sm:px-4 sm:[grid-template-columns:20px_1fr_92px_80px]"
        >
          <span></span>
          <span>Line item</span>
          <span class="text-right text-emerald-600">Cost</span>
          <span class="text-right">Tokens</span>
        </div>
        <div
          v-for="r in bill.rows"
          :key="r.key"
          class="grid items-center gap-x-2 border-b border-slate-100 px-3 py-2 [grid-template-columns:20px_1fr_74px_64px] sm:gap-x-3 sm:px-4 sm:[grid-template-columns:20px_1fr_92px_80px]"
        >
          <span class="flex justify-center">
            <span class="h-2.5 w-2.5 rounded-sm" :class="r.accent"></span>
          </span>
          <span class="min-w-0">
            <span class="block truncate text-[13px] font-semibold text-slate-800">{{ r.label }}</span>
            <span class="block truncate text-[10.5px] text-slate-400">{{ r.sub }}</span>
          </span>
          <span class="text-right">
            <span class="block font-mono text-[13px] font-bold tabular-nums text-emerald-600">{{ fmtCost(r.costUsd) }}</span>
            <span class="block font-mono text-[10px] text-slate-400">{{ r.pct }}</span>
          </span>
          <span class="text-right font-mono text-[12.5px] tabular-nums text-slate-700">{{ fmtTokens(r.tokenCount) }}</span>
        </div>
        <div
          class="grid items-center gap-x-2 bg-slate-50 px-3 py-2.5 [grid-template-columns:20px_1fr_74px_64px] sm:gap-x-3 sm:px-4 sm:[grid-template-columns:20px_1fr_92px_80px]"
          data-testid="spend-bill-total"
        >
          <span></span>
          <span class="text-[11px] font-bold uppercase tracking-[0.04em] text-slate-700">Total spend</span>
          <span class="text-right font-mono text-sm font-bold tabular-nums text-emerald-600">{{ bill.totalCost }}</span>
          <span class="text-right font-mono text-[12.5px] font-bold tabular-nums text-slate-900">{{ bill.totalTokens }}</span>
        </div>
      </section>
    </div>
  </div>
</template>
