<script setup>
import { computed } from 'vue'
import { fmtTokens } from '../../utils/traceFormatters.js'

// Shared chip row for a turn's secondary counts — rendered inside the mini
// rail's hover flyout and, on hover, under an expanded TOC card.
const props = defineProps({
  metrics: { type: Object, required: true },
  chipClass: { type: String, default: 'rounded px-1.5 py-px' },
})

const chips = computed(() => {
  const m = props.metrics
  const out = []
  if (m.spans) out.push({ text: `${m.spans} spans`, agent: false })
  if (m.tools) out.push({ text: `${m.tools} tools`, agent: false })
  if (m.agents) out.push({ text: `${m.agents} agents`, agent: true })
  if (m.inputTokens) out.push({ text: `${fmtTokens(m.inputTokens)}/${fmtTokens(m.outputTokens)}`, agent: false })
  if (m.ctxPct != null) out.push({ text: `ctx ${m.ctxPct}%`, agent: false })
  return out
})
</script>

<template>
  <div class="flex flex-wrap items-center gap-1 font-mono">
    <span
      v-for="c in chips"
      :key="c.text"
      class="whitespace-nowrap"
      :class="[chipClass, c.agent ? 'bg-violet-50 text-violet-700' : 'bg-slate-100 text-slate-600']"
    >{{ c.text }}</span>
  </div>
</template>
