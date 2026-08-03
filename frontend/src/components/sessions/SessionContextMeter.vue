<script setup>
import { computed } from 'vue'
import { fmtTokens } from '../../utils/traceFormatters.js'
import { contextTone } from '../../utils/sessionRowFormat.js'

const props = defineProps({
  s: { type: Object, required: true },
})

const peak = computed(() => props.s.peak_main_context_tokens || props.s.peak_context_tokens)
const tone = computed(() => contextTone(props.s.context_pct))
// A 0.4% session must still show a sliver of fill, or the meter reads as
// "no data" — which is what the `-` placeholder already means.
const width = computed(() => `${Math.max(2, Math.min(100, props.s.context_pct || 0))}%`)

// Only surfaced when the all-inclusive peak (advisor / sub-call tokens rolled
// in) diverges from the main conversation by more than a point, so the normal
// case stays uncluttered.
const subPct = computed(() => {
  const { context_pct: main, context_pct_all: all } = props.s
  return main != null && all != null && all - main > 1 ? all : null
})
</script>

<template>
  <div v-if="s.context_pct != null" class="ctx">
    <div class="ctx__top">
      <span class="ctx__track" aria-hidden="true">
        <span class="ctx__fill" :class="`ctx__fill--${tone}`" :style="{ width }"></span>
      </span>
      <span class="ctx__pct" :class="`ctx__pct--${tone}`">{{ s.context_pct }}%</span>
      <span
        v-if="subPct != null"
        class="ctx__sub"
        :title="`includes advisor/sub-call tokens — peak ${fmtTokens(s.peak_context_tokens)} / ${fmtTokens(s.context_window_tokens)}`"
      >+sub {{ subPct }}%</span>
    </div>
    <div
      class="ctx__tokens"
      :title="`main-conversation peak ${fmtTokens(peak)} / ${fmtTokens(s.context_window_tokens)} tokens`"
    >{{ fmtTokens(peak) }} <span class="ctx__slash">/</span> {{ fmtTokens(s.context_window_tokens) }}</div>
  </div>
  <span v-else class="ctx__empty" title="No usage recorded for this session">-</span>
</template>

<style scoped>
.ctx { min-width: 0; }
.ctx__top {
  align-items: center;
  display: flex;
  gap: 0.375rem;
}
.ctx__track {
  background: var(--color-surface-3);
  border-radius: 9999px;
  display: inline-block;
  flex: 0 0 auto;
  height: 0.25rem;
  overflow: hidden;
  width: 2.75rem;
}
.ctx__fill {
  border-radius: 9999px;
  display: block;
  height: 100%;
}
.ctx__fill--ok { background: var(--color-emerald-500); }
.ctx__fill--warn { background: var(--color-amber-500); }
.ctx__fill--danger { background: var(--color-red-500); }
.ctx__pct {
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.ctx__pct--ok { color: var(--color-emerald-700); }
.ctx__pct--warn { color: var(--color-amber-700); }
.ctx__pct--danger { color: var(--color-red-700); }
.ctx__sub {
  color: var(--color-fg-faint);
  cursor: help;
  font-size: 0.625rem;
  font-weight: 600;
}
.ctx__tokens {
  color: var(--color-fg-faint);
  font-size: 0.6875rem;
  font-variant-numeric: tabular-nums;
  margin-top: 0.125rem;
  white-space: nowrap;
}
.ctx__slash { opacity: 0.6; }
.ctx__empty { color: var(--color-fg-faint); font-size: 0.75rem; }
</style>
