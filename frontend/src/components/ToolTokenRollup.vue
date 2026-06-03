<script setup>
// "Tokens by tool" rollup, extracted from SessionTraceView. Purely
// presentational: it takes the raw per-session rollup payload (fetched by the
// parent from /sessions/:id/tool-rollup) and owns its own derived views +
// collapse/grouping UI state. No selection or loading coupling.
import { ref, computed } from 'vue'
import { fmtTokens, fmtCost, toolDisplayLabel, toolBadge } from '../utils/traceFormatters.js'

const props = defineProps({
  // The raw payload: { rollup: [{name, input_tokens, output_tokens, cost_usd,
  // calls, targets:[{target,label,tokens,calls,span_id}]}], attributed_*,
  // untagged_* }. null until loaded.
  rollupData: { type: Object, default: null },
})

// Clicking a drill-down target jumps to its most expensive call's span.
const emit = defineEmits(['jump-span'])

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
      // Top targets (files/commands) by input-token cost — the drill-down.
      targets: Array.isArray(t.targets) ? t.targets : [],
    }
  })
})

// Per-tool drill-down expand state (flat 'tokens' view), keyed by fullName.
const expandedTools = ref({})
const toggleTool = (name) => { expandedTools.value[name] = !expandedTools.value[name] }
const isToolExpanded = (name) => !!expandedTools.value[name]

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

    <!-- Flat view: tools sorted by spend; tools with file/command targets
         expand to a per-target token drill-down (which file/command cost most). -->
    <div v-else-if="rollupExpanded" class="flex flex-col gap-0.5 font-mono text-[11px]">
      <div v-for="tool in toolTokenSorted" :key="tool.fullName">
        <button
          v-if="tool.targets.length"
          type="button"
          class="flex items-center gap-1.5 w-full text-left py-0.5 -mx-1 px-1 rounded hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
          :aria-expanded="isToolExpanded(tool.fullName)"
          @click="toggleTool(tool.fullName)"
        >
          <svg
            class="w-2.5 h-2.5 text-slate-400 transition-transform shrink-0"
            :class="isToolExpanded(tool.fullName) ? 'rotate-90' : ''"
            viewBox="0 0 12 12" fill="none" aria-hidden="true"
          >
            <path d="M4.5 2.5 8 6l-3.5 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <span class="inline-block text-[9px] font-semibold uppercase tracking-wider px-1 py-px rounded" :class="tool.badge.classes">{{ tool.badge.label }}</span>
          <span class="text-slate-600">{{ tool.name }}</span>
          <span class="text-slate-400">{{ fmtTokens(tool.input + tool.output) }}</span>
          <span class="text-slate-300">· {{ tool.n }}×</span>
        </button>
        <div v-else class="flex items-center gap-1.5 py-0.5 -mx-1 px-1">
          <span class="w-2.5 shrink-0" aria-hidden="true"></span>
          <span class="inline-block text-[9px] font-semibold uppercase tracking-wider px-1 py-px rounded" :class="tool.badge.classes">{{ tool.badge.label }}</span>
          <span class="text-slate-600">{{ tool.name }}</span>
          <span class="text-slate-400">{{ fmtTokens(tool.input + tool.output) }}</span>
        </div>
        <ul v-if="isToolExpanded(tool.fullName)" class="ml-5 mb-1 mt-0.5 flex flex-col gap-0.5">
          <li v-for="tg in tool.targets" :key="tg.target">
            <button
              type="button"
              class="flex items-center gap-2 w-full text-left -mx-1 px-1 py-px rounded hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 disabled:hover:bg-transparent"
              :disabled="!tg.span_id"
              :title="tg.span_id ? tg.target + '  ·  click to jump to its most expensive call' : tg.target"
              @click="emit('jump-span', tg.span_id)"
            >
              <span class="shrink-0 w-14 text-right tabular-nums text-slate-600 font-medium">{{ fmtTokens(tg.tokens) }}</span>
              <span v-if="tg.calls > 1" class="shrink-0 w-7 text-slate-300">{{ tg.calls }}×</span>
              <span v-else class="shrink-0 w-7" aria-hidden="true"></span>
              <span class="truncate min-w-0 flex-1 text-slate-500">{{ tg.label }}</span>
            </button>
          </li>
        </ul>
      </div>
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
