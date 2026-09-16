<script setup>
import { computed } from 'vue'
import Button from '../../ui/Button.vue'
import { foldCategory, FOLD_CATEGORY_LABELS } from '../dispatch.js'

// The anchor for spans a detail mode set aside: one per turn, sitting where
// the turn's FIRST set-aside span occurred. It is an anchor, not a container —
// expanding restores each span to its own position on the spine, so a reader
// who opens it is reading the same session order, just louder.
//
// Dashed on the page ground rather than tinted: a tinted strip reads as a
// state (warning / loading), and it would spend a semantic colour on a row
// whose whole point is that nothing is wrong.
//
// No numeral anywhere says how many spans are behind it. "25 hidden" reads as
// an error budget and invites counting instead of reading; the proportion bar
// and the category names carry the volume.
const props = defineProps({
  spans: { type: Array, required: true },
  expanded: { type: Boolean, default: false },
  // 'fold' — Condensed's tier-3 strip. 'latest' — Focus's strip, which holds
  // everything but the turn's one card: its edits and shell runs as well as its
  // noise. The noun changes because "background event" would be describing work
  // the agent genuinely did.
  variant: { type: String, default: 'fold' },
})
defineEmits(['toggle'])

const isLatest = computed(() => props.variant === 'latest')
const noun = computed(() => {
  const one = props.spans.length === 1
  if (isLatest.value) return one ? 'one other span' : 'the rest of this turn'
  return one ? 'background event' : 'background events'
})
const hint = computed(() => (isLatest.value
  ? 'Set aside by Focus — click to put them back on the spine'
  : 'Set aside by Condensed — click to put them back on the spine'))

// Ordered by volume so "mostly lint" vs "mostly injection" is one glance.
const mix = computed(() => {
  const counts = new Map()
  for (const span of props.spans) {
    const key = foldCategory(span)
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  return [...counts.entries()]
    .map(([key, count]) => ({ key, count, label: FOLD_CATEGORY_LABELS[key] || key }))
    .sort((a, b) => b.count - a.count)
})

// Greyscale only — colour here would collide with the semantic palette the
// spine already spends. The tone is keyed on the CATEGORY, not on the
// segment's rank in this turn: rank-keyed tones made `thinking` dark in a
// thinking-heavy turn and pale in a rule-heavy one, so the bar carried no
// identity and two turns could not be compared. `slate-100` is dropped —
// against the strip's own `bg-background` (slate-50 light, and the inverted
// near-black under dark) it was invisible in both themes.
const TONES = {
  rule: 'bg-slate-500', thinking: 'bg-slate-400', harness: 'bg-slate-300',
  memory: 'bg-slate-600', tools: 'bg-slate-300', skills: 'bg-slate-500',
  edits: 'bg-slate-600', replies: 'bg-slate-500', failures: 'bg-slate-700',
  agents: 'bg-slate-400', 'tool calls': 'bg-slate-300',
  context: 'bg-slate-200',
}
const bar = computed(() => mix.value.map((m) => ({
  ...m,
  tone: TONES[m.key] || TONES.context,
  pct: (m.count / props.spans.length) * 100,
})))

// What went wrong behind the strip, as one mark. Two things can be in there
// and neither survives the mix bar on its own: a violating `rule.check`, whose
// `attributes.status === 'violation'` is the same predicate RuleCheckRow reads
// (its `status_code` stays 'OK' even on a hit, so a status-code test finds
// nothing, ever), and — since Focus stopped headlining failures the turn
// recovered from — a failed span. Both would otherwise be a grey sliver in a
// 50-row turn and a word in a truncating list.
//
// The counts live in the tooltip, not in the label: on screen they would be
// numerals describing spans the reader cannot see.
const plural = (n, one) => `${n} ${one}${n === 1 ? '' : 's'}`

const marks = computed(() => {
  const violations = props.spans.filter(
    (s) => s.name === 'rule.check' && s.attributes?.status === 'violation').length
  const failures = props.spans.filter((s) => foldCategory(s) === 'failures').length
  const parts = []
  if (failures) parts.push(plural(failures, 'failure'))
  if (violations) parts.push(plural(violations, 'rule violation'))
  return parts.length ? `${parts.join(', ')} in this turn` : ''
})

const mixText = computed(() => mix.value.map((m) => m.label).join(' · '))
</script>

<template>
  <Button
    variant="ghost"
    size="sm"
    data-testid="fold-strip"
    class="flex h-auto w-full items-center justify-start gap-3 rounded-md border border-dashed border-border bg-background px-2.5 py-1 text-left text-[11.5px] font-medium text-fg-subtle hover:border-border-strong hover:bg-background hover:text-fg-muted"
    :aria-expanded="expanded"
    :title="hint"
    @click="$emit('toggle')"
  >
    <span class="shrink-0 font-mono text-[9px] leading-none text-fg-faint" aria-hidden="true">{{ expanded ? '▾' : '▸' }}</span>
    <span class="shrink-0">{{ noun }}<template v-if="expanded"> · shown in place</template></span>
    <!-- The bar is decorative; the same breakdown follows as text so it
         survives greyscale, screen readers, and a narrow column. -->
    <span
      v-if="!expanded"
      aria-hidden="true"
      class="hidden sm:flex h-[5px] w-24 shrink-0 overflow-hidden rounded-full bg-surface-2"
    >
      <span
        v-for="seg in bar"
        :key="seg.key"
        class="h-full"
        :class="seg.tone"
        :style="{ width: `${seg.pct}%` }"
      ></span>
    </span>
    <span class="min-w-0 flex-1 truncate text-left text-fg-faint">{{ mixText }}</span>
    <span
      v-if="marks"
      class="shrink-0 rounded border border-amber-200 bg-amber-100 px-1 text-[10px] font-semibold text-amber-800"
      :title="marks"
    >⚠</span>
    <span class="shrink-0 font-mono text-fg-faint">{{ expanded ? 'hide' : 'show all' }}</span>
  </Button>
</template>
