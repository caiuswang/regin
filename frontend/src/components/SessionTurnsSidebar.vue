<script setup>
// Per-API-call "Turns" panel, extracted from SessionTraceView: a collapsible
// list of turn rows (clock, duration, consumption bar, fresh-in/out tokens,
// ctx%, effort), each expandable to a per-span drill-down + token breakdown.
//
// Purely presentational: the parent still owns turn data, selection, scroll
// sync, and lazy loading. This component renders + emits intent
// (load / toggle-collapsed / toggle-expanded / select-turn / select-span-ref /
// store-row) so none of the coupled selection/scroll logic moves.
import { fmtTokens } from '../utils/traceFormatters.js'
import { barColor, toolBadgeColor } from '../utils/spanColors.js'
import Card from './Card.vue'

const props = defineProps({
  turns: { type: Array, default: null },
  turnsCollapsed: { type: Boolean, default: false },
  turnsStale: { type: Boolean, default: false },
  turnsLoading: { type: Boolean, default: false },
  selectedTurnUuid: { type: [String, null], default: null },
  expandedTurnUuid: { type: [String, null], default: null },
  selectedSpan: { type: Object, default: null },
  maxTurnConsumption: { type: Number, default: 0 },
})

defineEmits(['load', 'toggle-collapsed', 'toggle-expanded', 'select-turn', 'select-span-ref', 'store-row'])

// Local clock/duration formatters — exact copies of SessionTraceView's (its
// traceFormatters siblings behave differently; see that file's note).
function fmtLocalClock(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}
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

// Fresh (newly-billed) input this turn = input + cache_creation; cache_read
// replays are ~10% price and dominate context_used, so they're excluded here.
function turnFreshInTokens(turn) {
  if (!turn) return 0
  return (turn.input_tokens || 0) + (turn.cache_creation_tokens || 0)
}

function turnConsumptionPct(turn) {
  const max = props.maxTurnConsumption
  if (!max || max <= 0) return 0
  const c = turnFreshInTokens(turn) + (turn.output_tokens || 0)
  return Math.max(0, Math.min(100, (c / max) * 100))
}

function turnCtxClass(pct) {
  if (pct == null) return 'bg-gray-200'
  if (pct >= 80) return 'bg-red-500'
  if (pct >= 50) return 'bg-amber-500'
  return 'bg-green-500'
}
</script>

<template>
  <Card class="mt-4">
    <div class="flex items-center justify-between mb-2">
      <button
        v-if="turns != null"
        type="button"
        class="flex items-center gap-1.5 text-sm font-semibold text-slate-700 hover:text-slate-900 focus-visible:outline-2 focus-visible:outline-blue-500"
        :title="turnsCollapsed
          ? (turnsStale ? 'Expand and refresh turns' : 'Expand turns list')
          : 'Collapse turns list'"
        @click="$emit('toggle-collapsed')"
      >
        <span class="inline-block w-3 text-xs text-slate-400">{{ turnsCollapsed ? '▸' : '▾' }}</span>
        Turns
        <span
          v-if="turnsCollapsed && turnsStale"
          class="text-[10px] text-amber-600 font-normal"
          title="Spans were reloaded while turns were folded — they'll refresh on expand."
        >· stale</span>
      </button>
      <h2 v-else class="text-sm font-semibold text-slate-700">Turns</h2>
      <button
        v-if="turns == null"
        type="button"
        class="text-xs text-blue-600 hover:underline focus-visible:outline-2 focus-visible:outline-blue-500"
        :disabled="turnsLoading"
        @click="$emit('load')"
      >{{ turnsLoading ? 'loading…' : 'load' }}</button>
      <span v-else class="text-xs text-gray-400">
        {{ turnsLoading ? 'loading…' : `${turns.length} turns` }}
      </span>
    </div>
    <p v-if="turns == null && !turnsLoading" class="text-xs text-gray-500">
      Per-API-call token usage. Click load to fetch.
    </p>
    <div v-else-if="turns && turns.length === 0 && !turnsCollapsed" class="text-xs text-gray-500">
      No turn-usage rows recorded for this session yet.
    </div>
    <ul v-else-if="turns && !turnsCollapsed" class="divide-y divide-gray-100">
      <li
        v-for="(t, i) in turns"
        :key="t.turn_uuid"
        :ref="(el) => $emit('store-row', t.turn_uuid, el)"
        class="py-1.5 cursor-pointer"
        :class="[
          selectedTurnUuid === t.turn_uuid
            ? 'bg-indigo-50 -mx-2 px-2 rounded'
            : 'hover:bg-gray-50 -mx-2 px-2 rounded',
        ]"
        @click="$emit('select-turn', t.turn_uuid)"
      >
        <!-- Row 1: index · clock · duration · per-turn consumption bar+numbers · ctx% tag -->
        <div class="flex items-center gap-2 text-[11px] font-mono leading-tight">
          <span class="text-gray-400 w-6 text-right shrink-0">#{{ t.turn_index }}</span>
          <span class="text-gray-600 shrink-0"
                :title="new Date(t.timestamp).toLocaleString()">{{ fmtLocalClock(t.timestamp) }}</span>
          <span class="text-gray-400 shrink-0 w-10 text-right"
                :title="'time since the previous turn — counts tool-use round-trips'">
            {{ t.duration_ms != null ? fmtDuration(t.duration_ms) : '—' }}
          </span>
          <span class="relative h-1.5 flex-1 bg-gray-100 rounded-sm overflow-hidden min-w-[2rem]"
                :title="'fresh input + output this turn (bar scaled to session max)'">
            <span
              class="absolute inset-y-0 left-0 rounded-sm bg-indigo-500"
              :style="{ width: turnConsumptionPct(t) + '%' }"
            ></span>
          </span>
          <span class="text-gray-700 font-medium shrink-0 text-right tabular-nums"
                :title="'fresh input this turn = input_tokens + cache_creation_tokens\n(the newly-billed input bytes; cache_read replays are ~10%)'">
            ↑{{ fmtTokens(turnFreshInTokens(t)) }}
          </span>
          <span class="text-gray-700 font-medium shrink-0 text-right tabular-nums"
                :title="'output_tokens — model-generated this turn'">
            ↓{{ fmtTokens(t.output_tokens) }}
          </span>
          <span v-if="t.ctx_pct != null"
                class="shrink-0 inline-flex items-center px-1 rounded text-[10px] gap-0.5"
                :class="t.is_server_side
                          ? 'bg-slate-200 text-slate-700 ring-1 ring-dashed ring-slate-400'
                          : turnCtxClass(t.ctx_pct) + ' text-white'"
                :title="t.is_server_side
                          ? 'advisor / server-side sub-call rollup — not main-conversation context (' + fmtTokens(t.context_used_tokens) + ' tokens charged to this turn)'
                          : 'context window used after this turn: ' + fmtTokens(t.context_used_tokens) + ' tokens'">
            <span v-if="t.is_server_side" class="text-[9px] uppercase tracking-tight">sub</span>
            {{ Math.round(t.ctx_pct) }}%
          </span>
          <span v-if="t.effort_level"
                class="shrink-0 inline-flex items-center px-1 rounded text-[10px] bg-violet-100 text-violet-700"
                :title="'reasoning effort level for this turn: ' + t.effort_level">
            {{ t.effort_level }}
          </span>
          <button
            type="button"
            class="text-slate-300 hover:text-slate-700 shrink-0 w-4 text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
            :aria-label="expandedTurnUuid === t.turn_uuid ? 'Collapse turn details' : 'Expand turn details'"
            :title="expandedTurnUuid === t.turn_uuid ? 'collapse' : 'show per-span breakdown'"
            @click.stop="$emit('toggle-expanded', t.turn_uuid)"
          >{{ expandedTurnUuid === t.turn_uuid ? '−' : '+' }}</button>
        </div>
        <!-- Row 2: tool summary — chips colored to match tree view. -->
        <div v-if="t.tool_summary && t.tool_summary.length"
             class="flex flex-wrap gap-1 mt-1 pl-8 text-[10px] leading-none">
          <span
            v-for="ts in t.tool_summary"
            :key="ts.name"
            class="inline-flex items-center gap-0.5 px-1 py-0.5 rounded text-white/95"
            :class="toolBadgeColor(ts.name)"
          >{{ ts.name }}<span v-if="ts.count > 1" class="opacity-75">×{{ ts.count }}</span></span>
          <span v-if="t.span_count === 0" class="text-gray-400 italic">no spans in this turn</span>
        </div>
        <div v-else-if="t.span_count === 0"
             class="mt-1 pl-8 text-[10px] text-gray-400 italic leading-none">
          no spans in this turn
        </div>
        <!-- Drill-down: every span in this turn, labeled + clickable. -->
        <div v-if="expandedTurnUuid === t.turn_uuid && t.span_refs && t.span_refs.length"
             class="mt-1.5 ml-8 border-l border-gray-200 pl-2 space-y-0.5">
          <div
            v-for="sr in t.span_refs"
            :key="sr.span_id"
            class="flex items-center gap-2 text-[10px] cursor-pointer hover:bg-white py-0.5 -mx-1 px-1 rounded"
            :class="selectedSpan && selectedSpan.span_id === sr.span_id ? 'bg-white ring-1 ring-indigo-300' : ''"
            @click.stop="$emit('select-span-ref', sr)"
          >
            <span
              class="inline-block w-1.5 h-1.5 rounded-full shrink-0"
              :class="barColor(sr.name)"
            ></span>
            <span class="text-gray-700 truncate flex-1">{{ sr.tool_name || sr.name }}</span>
            <span class="text-gray-400 shrink-0 font-mono">{{ fmtLocalClock(sr.start_time) }}</span>
          </div>
        </div>
        <!-- Row 3 (expanded): full token breakdown the top row elides. -->
        <div v-if="expandedTurnUuid === t.turn_uuid"
             class="mt-1.5 ml-8 text-[10px] text-gray-500 font-mono grid grid-cols-5 gap-2">
          <div :title="'fresh (uncached) input this turn'">
            <span class="text-gray-400">in</span> {{ fmtTokens(t.input_tokens) }}</div>
          <div :title="'cache_creation — new bytes written into the prompt cache this turn'">
            <span class="text-gray-400">cW</span> {{ fmtTokens(t.cache_creation_tokens) }}</div>
          <div :title="'output_tokens — model-generated this turn'">
            <span class="text-gray-400">out</span> {{ fmtTokens(t.output_tokens) }}</div>
          <div :title="'cache_read — replayed from prompt cache (~10% price, not a per-turn cost driver)'">
            <span class="text-gray-400">cR</span> {{ fmtTokens(t.cache_read_tokens) }}</div>
          <div :title="'context_used = in + cR + cW — size of the prompt sent to the model this turn'">
            <span class="text-gray-400">ctx</span> {{ fmtTokens(t.context_used_tokens) }}</div>
        </div>
      </li>
    </ul>
  </Card>
</template>
