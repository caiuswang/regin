<script setup>
// "Spend by tool" — the session's tools ranked by dollar cost.
//
// Ranked by cost rather than tokens on purpose: cache reads dominate the token
// count but bill ~10x cheaper, so a token-sorted list puts the cheap tools on
// top and tells the wrong story. The token column keeps a `peak` tag on the
// heaviest token consumer so both readings stay available in one table.
//
// Rows with drill-down targets expand to the per-file / per-command breakdown
// and each target jumps to its span, so nothing the old chip grid could reach
// became unreachable.
import { ref, computed } from 'vue'
import { fmtTokens, fmtCost, toolDisplayLabel, toolBadge } from '../utils/traceFormatters.js'
import Button from './ui/Button.vue'

const props = defineProps({
  // Raw /tool-rollup payload. Null until the fetch lands.
  rollupData: { type: Object, default: null },
})

const emit = defineEmits(['jump-span'])

const expanded = ref({})
function toggle(name) { expanded.value[name] = !expanded.value[name] }

// Medal styling for the podium; everything below rank 3 gets a plain numeral.
const MEDALS = [
  { bg: 'bg-amber-100', text: 'text-amber-700' },
  { bg: 'bg-slate-200', text: 'text-slate-600' },
  { bg: 'bg-orange-100', text: 'text-orange-700' },
]

const rows = computed(() => {
  const raw = props.rollupData?.rollup
  if (!Array.isArray(raw) || !raw.length) return []
  const tools = raw.map(t => {
    const badge = toolBadge(t.name)
    let name = t.name
    if ((t.name || '').startsWith('mcp__')) name = toolDisplayLabel(t.name)
    else if (t.name === 'assistant_thinking') name = 'thinking'
    return {
      name,
      fullName: t.name,
      group: badge.group,
      cost: t.cost_usd || 0,
      input: t.input_tokens || 0,
      output: t.output_tokens || 0,
      tokens: (t.input_tokens || 0) + (t.output_tokens || 0),
      calls: t.calls || 0,
      targets: Array.isArray(t.targets) ? t.targets : [],
    }
  }).sort((a, b) => b.cost - a.cost)

  const costTotal = tools.reduce((s, t) => s + t.cost, 0) || 1
  const peakTokens = Math.max(...tools.map(t => t.tokens))
  return tools.map((t, i) => ({
    ...t,
    rank: i + 1,
    medal: MEDALS[i] || null,
    pct: Math.round((t.cost / costTotal) * 100) + '%',
    // Only tag a real peak: an all-zero-token rollup would otherwise flag row 1.
    isPeak: peakTokens > 0 && t.tokens === peakTokens,
    inout: `${fmtTokens(t.input)} / ${fmtTokens(t.output)}`,
  }))
})

const attributed = computed(() => {
  const d = props.rollupData
  if (!d) return null
  const total = d.total_spend_usd ?? d.session_cost_usd
  return {
    attributed: fmtCost(d.attributed_cost_usd || 0),
    total: Number.isFinite(total) ? fmtCost(total) : null,
    count: rows.value.length,
  }
})
</script>

<template>
  <section class="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.03)]">
    <header class="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-4 pb-3 pt-3.5">
      <h4 class="m-0 text-[10px] font-semibold uppercase tracking-[0.09em] text-slate-400">Spend by tool</h4>
      <p
        v-if="attributed"
        class="m-0 text-[11.5px] text-slate-400"
        title="attributed = the share of the bill this rollup could pin to a specific tool; the remainder is cache replay and untagged output"
      >
        <span class="font-semibold text-emerald-600">{{ attributed.attributed }}</span>
        <template v-if="attributed.total"> of {{ attributed.total }}</template>
        attributed · {{ attributed.count }} tool<span v-if="attributed.count !== 1">s</span>
      </p>
    </header>

    <p v-if="!rows.length" class="m-0 px-4 pb-4 text-[12.5px] text-slate-400">
      No tool spend recorded for this session yet.
    </p>

    <template v-else>
      <div
        class="grid items-center gap-x-3 border-b border-slate-200 px-4 pb-2 text-[9.5px] font-semibold uppercase tracking-[0.06em] text-slate-400 [grid-template-columns:32px_1fr_84px_96px_44px]"
      >
        <span>#</span>
        <span>Tool</span>
        <span class="text-right text-emerald-600">Cost</span>
        <span class="text-right">Tokens</span>
        <span class="text-right">Calls</span>
      </div>
      <div v-for="r in rows" :key="r.fullName">
        <!-- Disabled when there is nothing to drill into: the primitive's
             `disabled:pointer-events-none` is what makes a target-less row
             read as inert without a second style path. -->
        <Button
          variant="ghost"
          size="sm"
          :disabled="!r.targets.length"
          :class="[
            'grid h-auto w-full items-center gap-x-3 gap-y-0 rounded-none border-b border-slate-100 px-4 py-2 text-left [grid-template-columns:32px_1fr_84px_96px_44px]',
            r.rank <= 3 ? 'bg-emerald-50/40' : '',
          ]"
          @click="toggle(r.fullName)"
        >
          <span
            class="inline-flex h-[21px] w-[21px] items-center justify-center rounded-full text-[11px] tabular-nums"
            :class="r.medal ? [r.medal.bg, r.medal.text, 'font-bold'] : 'text-slate-400'"
          >{{ r.rank }}</span>
          <span class="min-w-0">
            <span class="block truncate text-[13px] font-semibold text-slate-800">{{ r.name }}</span>
            <span class="block truncate text-[10.5px] text-slate-400">{{ r.group }}</span>
          </span>
          <span class="text-right">
            <span class="block font-mono text-[13px] font-bold tabular-nums text-emerald-600">{{ fmtCost(r.cost) }}</span>
            <span class="block font-mono text-[10px] text-slate-400">{{ r.pct }}</span>
          </span>
          <span class="text-right">
            <span class="block font-mono text-[12.5px] tabular-nums text-slate-700"><span
              v-if="r.isPeak"
              class="mr-1.5 inline-block rounded bg-amber-100 px-1 align-middle font-sans text-[8px] font-bold uppercase tracking-[0.04em] text-amber-700"
              title="heaviest token consumer this session"
            >peak</span>{{ fmtTokens(r.tokens) }}</span>
            <span class="block font-mono text-[10px] text-slate-400">{{ r.inout }}</span>
          </span>
          <span class="text-right font-mono text-[12.5px] tabular-nums text-slate-500">{{ r.calls }}×</span>
        </Button>
        <!-- Drill-down: the files/commands this tool touched, each jumping to
             its most expensive call. -->
        <div v-if="expanded[r.fullName]" class="border-b border-slate-100 bg-slate-50 py-1 pl-[76px] pr-4">
          <Button
            v-for="g in r.targets"
            :key="g.target || g.label"
            variant="ghost"
            size="sm"
            class="flex h-auto w-full items-baseline justify-start gap-2 rounded px-1 py-1 text-left text-[12px] font-normal hover:bg-white"
            @click="emit('jump-span', g.span_id)"
          >
            <span class="min-w-0 flex-1 truncate font-mono text-slate-600" :title="g.label || g.target">{{ g.label || g.target }}</span>
            <span class="shrink-0 font-mono text-[11px] tabular-nums text-slate-400">{{ fmtTokens(g.tokens) }}</span>
            <span v-if="g.calls > 1" class="shrink-0 font-mono text-[11px] tabular-nums text-slate-400">{{ g.calls }}×</span>
          </Button>
        </div>
      </div>
    </template>
  </section>
</template>
