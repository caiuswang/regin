<script setup>
import { ref, computed } from 'vue'
import Button from '../ui/Button.vue'
import { fmtClock, fmtCost, fmtTokens } from '../../utils/traceFormatters.js'

// Turn footer: one filled bar closing the turn. Carries the rollup of every
// API turn this prompt drove (with a per-turn disclosure list) on the left and
// the turn's own expand/collapse trigger on the right. The per-turn disclosure
// is per-prompt presentational state, so it lives here, not in the
// orchestrator. `item` is null on a scoped feed, where turn usage — main-
// session bookkeeping — isn't projected; the bar still renders for the trigger.
const props = defineProps({
  // One turnItems entry (useSpanTree): { turns, turnAgg, ... }, or null.
  item: { type: Object, default: null },
  contextWindowTokens: { type: Number, default: null },
  // Whether the turn's event spine is open — drives the `hide/show detail` label.
  detailOpen: { type: Boolean, default: false },
})
defineEmits(['toggle-detail'])

const expanded = ref(false)
const agg = computed(() => props.item?.turnAgg || null)
const turns = computed(() => props.item?.turns || [])

function turnCtxPct(turn) {
  if (!turn || !turn.context_used_tokens || !props.contextWindowTokens) return null
  const window = props.contextWindowTokens
  if (window <= 0) return null
  return Math.max(0, Math.min(100, (turn.context_used_tokens / window) * 100))
}
</script>

<template>
  <!-- The filled bar IS the usage rollup, so it only earns its slab when there
       is usage to put in it. With no `agg` — a scoped feed, or the common case
       where the reader hasn't opened the Turns sidebar (that fetch is
       deliberately deferred; per-turn aggregation is expensive) — it would
       render as an empty grey block holding nothing but the collapse trigger,
       so drop to the bare trigger instead. -->
  <div
    class="mt-0.5 font-mono text-[11px] text-slate-500"
    :class="agg ? 'rounded-lg bg-slate-50 px-2.5 py-2' : 'px-0.5 py-0.5'"
  >
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
      <template v-if="agg">
        <span class="uppercase tracking-[0.06em] text-[10px] font-semibold text-slate-400">Turn usage</span>
        <Button
          variant="ghost"
          class="h-auto px-1 -mx-1 py-0 font-mono text-[11px] font-normal text-slate-500 hover:bg-transparent hover:text-slate-700"
          :title="'API turns #' + turns[0].turn_index + '–#' + agg.lastTurn.turn_index + ' answered this prompt — click to list them'"
          :aria-expanded="expanded"
          @click="expanded = !expanded"
        >{{ agg.count }} {{ agg.count === 1 ? 'turn' : 'turns' }} {{ expanded ? '▴' : '▾' }}</Button>
        <span>in {{ fmtTokens(agg.inputTokens) }}</span>
        <span>out {{ fmtTokens(agg.outputTokens) }}</span>
        <span
          v-if="turnCtxPct(agg.lastTurn) != null"
          title="context occupancy after this prompt's last turn"
        >ctx {{ Math.round(turnCtxPct(agg.lastTurn)) }}%</span>
        <span v-if="agg.costUsd != null" class="text-emerald-600 font-semibold">{{ fmtCost(agg.costUsd) }}</span>
        <span
          v-if="agg.lastTurn.effort_level"
          class="inline-flex items-center px-1 rounded text-[10px] bg-violet-100 text-violet-700"
          :title="'reasoning effort level on this prompt\'s last turn: ' + agg.lastTurn.effort_level"
        >{{ agg.lastTurn.effort_level }}</span>
      </template>
      <!-- Only while open: the collapsed turn's `show detail ▾` sits with its
           chips, so there is exactly one trigger on screen at a time. -->
      <Button
        v-if="detailOpen"
        variant="ghost"
        class="ml-auto h-auto px-1 -mr-1 py-0 font-mono text-[11px] font-normal text-slate-400 hover:bg-transparent hover:text-slate-700"
        :aria-expanded="detailOpen"
        @click="$emit('toggle-detail')"
      >hide detail ▴</Button>
    </div>
    <div
      v-if="expanded && agg"
      class="mt-1.5 space-y-0.5 text-[10px]"
    >
      <div
        v-for="t in turns"
        :key="t.turn_uuid"
        class="flex items-center gap-2"
      >
        <span class="w-8 text-right shrink-0">#{{ t.turn_index }}</span>
        <span>{{ fmtClock(t.timestamp) }}</span>
        <span>↑{{ fmtTokens((t.input_tokens || 0) + (t.cache_creation_tokens || 0)) }}</span>
        <span>↓{{ fmtTokens(t.output_tokens || 0) }}</span>
        <span v-if="turnCtxPct(t) != null">ctx {{ Math.round(turnCtxPct(t)) }}%</span>
      </div>
    </div>
  </div>
</template>
