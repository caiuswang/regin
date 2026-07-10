<script setup>
import { ref, computed } from 'vue'
import Button from '../ui/Button.vue'
import { fmtClock, fmtTokens } from '../../utils/traceFormatters.js'

// Turn-metadata footer under one prompt group: the rollup of every API turn
// that prompt drove, with a per-turn disclosure list. The disclosure is
// per-prompt presentational state, so it lives here, not in the orchestrator.
const props = defineProps({
  // One turnItems entry (useSpanTree): { turns, turnAgg, ... }.
  item: { type: Object, required: true },
  contextWindowTokens: { type: Number, default: null },
})

const expanded = ref(false)
const agg = computed(() => props.item.turnAgg)

function turnCtxPct(turn) {
  if (!turn || !turn.context_used_tokens || !props.contextWindowTokens) return null
  const window = props.contextWindowTokens
  if (window <= 0) return null
  return Math.max(0, Math.min(100, (turn.context_used_tokens / window) * 100))
}
</script>

<template>
  <div class="text-[11px] text-slate-400 pl-2">
    <div class="flex items-center gap-2">
      <Button
        variant="ghost"
        class="h-auto px-1 -mx-1 py-0 text-[11px] font-normal text-slate-400 hover:bg-transparent hover:text-slate-700"
        :title="'API turns #' + item.turns[0].turn_index + '–#' + agg.lastTurn.turn_index + ' answered this prompt — click to list them'"
        :aria-expanded="expanded"
        @click="expanded = !expanded"
      >{{ agg.count }} {{ agg.count === 1 ? 'turn' : 'turns' }} {{ expanded ? '▴' : '▾' }}</Button>
      <span class="text-slate-300">·</span>
      <span>↑{{ fmtTokens(agg.inputTokens) }}</span>
      <span>↓{{ fmtTokens(agg.outputTokens) }}</span>
      <span
        v-if="turnCtxPct(agg.lastTurn) != null"
        class="inline-flex items-center px-1 rounded text-[10px] text-white"
        :class="turnCtxPct(agg.lastTurn) >= 80
          ? 'bg-red-500'
          : turnCtxPct(agg.lastTurn) >= 50
            ? 'bg-amber-500'
            : 'bg-green-500'"
        title="context occupancy after this prompt's last turn"
      >{{ Math.round(turnCtxPct(agg.lastTurn)) }}%</span>
      <span
        v-if="agg.lastTurn.effort_level"
        class="inline-flex items-center px-1 rounded text-[10px] bg-violet-100 text-violet-700"
        :title="'reasoning effort level on this prompt\'s last turn: ' + agg.lastTurn.effort_level"
      >{{ agg.lastTurn.effort_level }}</span>
    </div>
    <div
      v-if="expanded"
      class="mt-1 space-y-0.5 font-mono text-[10px]"
    >
      <div
        v-for="t in item.turns"
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
