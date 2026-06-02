<script setup>
// "Tokens by tool" rollup, extracted from SessionTraceView. Purely
// presentational: it takes the raw per-session rollup payload (fetched by the
// parent from /sessions/:id/tool-rollup) and owns its own derived views +
// collapse/grouping UI state. No selection or loading coupling.
import { ref, computed } from 'vue'
import { fmtTokens, fmtCost, toolDisplayLabel, toolBadge } from '../utils/traceFormatters.js'

const props = defineProps({
  // The raw payload: { rollup: [{name, input_tokens, output_tokens, cost_usd,
  // calls}], attributed_*, untagged_* }. null until loaded.
  rollupData: { type: Object, default: null },
})

// Normalized per-tool rows. Each badge doubles as a group definition for the
// grouped view (`group` = cluster name, `order` = tiebreak).
const toolTokenRollup = computed(() => {
  const raw = props.rollupData
  if (!raw || !Array.isArray(raw.rollup)) return []
  return raw.rollup.map(t => {
    const isMcp = (t.name || '').startsWith('mcp__')
    const isAssistantText = t.name === 'assistant_text'
    const isAssistantThinking = t.name === 'assistant_thinking'
    let displayName = t.name
    if (isMcp) displayName = toolDisplayLabel(t.name)
    else if (isAssistantThinking) displayName = 'thinking'
    return {
      name: displayName,
      fullName: t.name,
      isMcp,
      isAssistantText,
      isAssistantThinking,
      badge: toolBadge(t.name),
      input: t.input_tokens || 0,
      output: t.output_tokens || 0,
      cost: t.cost_usd || 0,
      n: t.calls || 0,
    }
  })
})

// Two ways to organize the rollup: 'groups' clusters tools by badge bucket
// with a per-group subtotal; 'tokens' is a flat list sorted by spend.
const rollupView = ref('tokens')

// Collapses to a one-line summary by default so it doesn't compete with the
// header usage chips; expand to reveal the view toggle and per-tool chips.
const rollupExpanded = ref(false)

const _toolTokens = (t) => (t.input || 0) + (t.output || 0)

// Flat list sorted by total tokens desc (the 'tokens' view).
const toolTokenSorted = computed(() =>
  [...toolTokenRollup.value].sort((a, b) => _toolTokens(b) - _toolTokens(a))
)

// Tools clustered by badge bucket (the 'groups' view). Groups sort by total
// tokens desc (badge `order` breaks ties); tools within a group by tokens desc.
const toolTokenGroups = computed(() => {
  const byKey = new Map()
  for (const tool of toolTokenRollup.value) {
    const badge = tool.badge
    let g = byKey.get(badge.label)
    if (!g) {
      g = { key: badge.label, label: badge.group, badge,
            order: badge.order, tools: [], input: 0, output: 0, cost: 0, n: 0 }
      byKey.set(badge.label, g)
    }
    g.tools.push(tool)
    g.input += tool.input
    g.output += tool.output
    g.cost += tool.cost
    g.n += tool.n
  }
  const groups = [...byKey.values()]
  for (const g of groups) {
    g.tools.sort((a, b) => _toolTokens(b) - _toolTokens(a))
  }
  groups.sort((a, b) => {
    const diff = (b.input + b.output) - (a.input + a.output)
    return diff !== 0 ? diff : a.order - b.order
  })
  return groups
})

const toolRollupSummary = computed(() => {
  const raw = props.rollupData
  if (!raw) {
    return { attributedIn: 0, attributedOut: 0, attributedCost: 0,
             untaggedIn: 0, untaggedOut: 0, hasData: false }
  }
  return {
    attributedIn: raw.attributed_input_tokens || 0,
    attributedOut: raw.attributed_output_tokens || 0,
    attributedCost: raw.attributed_cost_usd || 0,
    untaggedIn: raw.untagged_input_tokens || 0,
    untaggedOut: raw.untagged_output_tokens || 0,
    hasData: Array.isArray(raw.rollup) && raw.rollup.length > 0,
  }
})
</script>

<template>
  <div
    v-if="toolRollupSummary.hasData"
    class="mb-4 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-xs"
  >
    <div class="flex items-center gap-2" :class="rollupExpanded ? 'mb-1.5' : ''">
      <button
        type="button"
        class="flex items-center gap-2 -mx-1 px-1 rounded transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
        :aria-expanded="rollupExpanded"
        @click="rollupExpanded = !rollupExpanded"
      >
        <svg
          class="w-3 h-3 text-slate-400 transition-transform"
          :class="rollupExpanded ? 'rotate-90' : ''"
          viewBox="0 0 12 12" fill="none" aria-hidden="true"
        >
          <path d="M4.5 2.5 8 6l-3.5 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <span class="font-medium text-slate-700">Tokens by tool</span>
        <span
          v-if="toolRollupSummary.attributedCost > 0"
          class="font-mono text-slate-500"
          :title="'cost computed from session model rates via models.dev'"
        >· {{ fmtCost(toolRollupSummary.attributedCost) }} attributed</span>
        <span class="font-mono text-slate-400">· {{ toolTokenRollup.length }} tools</span>
      </button>
      <div
        v-if="rollupExpanded"
        class="ml-auto inline-flex rounded-md border border-slate-200 overflow-hidden text-[10px] font-medium"
      >
        <button
          type="button"
          class="px-2 py-0.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-inset"
          :class="rollupView === 'groups' ? 'bg-slate-700 text-white' : 'bg-white text-slate-500 hover:bg-slate-50'"
          @click="rollupView = 'groups'"
        >Groups</button>
        <button
          type="button"
          class="px-2 py-0.5 transition-colors border-l border-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-inset"
          :class="rollupView === 'tokens' ? 'bg-slate-700 text-white' : 'bg-white text-slate-500 hover:bg-slate-50'"
          @click="rollupView = 'tokens'"
        >By tokens</button>
      </div>
    </div>

    <!-- Grouped view: one row per badge bucket, leading label carries the
         group subtotal, followed by the per-tool chips. -->
    <div v-if="rollupExpanded && rollupView === 'groups'" class="flex flex-col gap-1">
      <div
        v-for="g in toolTokenGroups"
        :key="g.key"
        class="flex items-start gap-x-3 font-mono text-[11px]"
      >
        <span
          class="inline-flex items-center gap-1.5 shrink-0 w-[164px]"
          :title="g.n + ' call(s) · in ' + g.input + ' · out ' + g.output + (g.cost ? ' · ' + fmtCost(g.cost) : '')"
        >
          <span
            class="inline-block text-[9px] font-semibold uppercase tracking-wider px-1 py-px rounded"
            :class="g.badge.classes"
          >{{ g.badge.label }}</span>
          <span class="font-sans text-slate-600">{{ g.label }}</span>
          <span class="text-slate-500 font-semibold">{{ fmtTokens(g.input + g.output) }}</span>
        </span>
        <!-- Chips wrap within their own column so the second line indents
             under the first chip instead of the group gutter. -->
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
          <span
            v-for="tool in g.tools"
            :key="tool.fullName"
            class="inline-flex items-center gap-1"
            :title="tool.fullName + ' — ' + tool.n + ' call(s) · in ' + tool.input + ' · out ' + tool.output + (tool.cost ? ' · ' + fmtCost(tool.cost) : '')"
          >
            <span class="text-slate-600">{{ tool.name }}</span>
            <span class="text-slate-400">{{ fmtTokens(tool.input + tool.output) }}</span>
          </span>
        </div>
      </div>
    </div>

    <!-- Flat view: every tool sorted by token spend, badge-prefixed. -->
    <div v-else-if="rollupExpanded" class="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px]">
      <span
        v-for="tool in toolTokenSorted"
        :key="tool.fullName"
        class="inline-flex items-center gap-1"
        :title="tool.fullName + ' — ' + tool.n + ' call(s) · in ' + tool.input + ' · out ' + tool.output + (tool.cost ? ' · ' + fmtCost(tool.cost) : '')"
      >
        <span
          class="inline-block text-[9px] font-semibold uppercase tracking-wider px-1 py-px rounded"
          :class="tool.badge.classes"
        >{{ tool.badge.label }}</span>
        <span class="text-slate-600">{{ tool.name }}</span>
        <span class="text-slate-400">{{ fmtTokens(tool.input + tool.output) }}</span>
      </span>
    </div>

    <!-- Untagged remainder: shown in both views as a trailing note. -->
    <div
      v-if="rollupExpanded && toolRollupSummary.untaggedIn + toolRollupSummary.untaggedOut > 0"
      class="mt-1.5 pt-1.5 border-t border-slate-100 font-mono text-[11px]"
    >
      <span
        class="inline-flex items-center gap-1"
        :title="'in: ' + toolRollupSummary.untaggedIn + ' · out: ' + toolRollupSummary.untaggedOut + ' — system prompt, conversation history, untracked prose'"
      >
        <span class="text-slate-400 italic">untagged</span>
        <span class="text-slate-400">{{ fmtTokens(toolRollupSummary.untaggedIn + toolRollupSummary.untaggedOut) }}</span>
      </span>
    </div>
  </div>
</template>
