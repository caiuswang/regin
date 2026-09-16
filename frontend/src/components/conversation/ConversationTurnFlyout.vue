<script setup>
import { computed, ref, onMounted, watch, nextTick } from 'vue'
import { fmtTime, fmtDuration, fmtCost } from '../../utils/traceFormatters.js'
import ConversationTurnMeta from './ConversationTurnMeta.vue'

// Hover detail for a collapsed (mini) TOC chip. `position: fixed` so it
// escapes the rail's own `overflow-y: auto`; the rail re-reads the chip's rect
// and re-feeds `anchor` on scroll/resize, so this only has to turn one rect
// into a clamped viewport position.
const props = defineProps({
  item: { type: Object, required: true },
  metrics: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
  // The hovered chip's live getBoundingClientRect(), flattened.
  anchor: { type: Object, required: true },
})

const WIDTH = 262
const GAP = 8

const el = ref(null)
const height = ref(0)

const style = computed(() => {
  const a = props.anchor
  const vw = window.innerWidth
  const vh = window.innerHeight
  // Flip to the chip's left when the rail sits close enough to the right edge
  // that the card would push the page sideways.
  const right = a.right + GAP
  const left = right + WIDTH > vw - GAP ? Math.max(GAP, a.left - GAP - WIDTH) : right
  const top = Math.max(GAP, Math.min(a.top, vh - height.value - GAP))
  return { left: `${left}px`, top: `${top}px`, width: `${WIDTH}px` }
})

async function measure() {
  await nextTick()
  height.value = el.value?.offsetHeight || 0
}
onMounted(measure)
watch(() => props.item.promptSpanId, measure)
</script>

<template>
  <!-- Teleported: the rail is `position: sticky`, which is its own stacking
       context, so a z-index inside it can't lift the card over the feed. -->
  <Teleport to="body">
  <div
    ref="el"
    class="fixed z-30 pointer-events-none rounded-[13px] border border-slate-200 bg-white px-[13px] py-3 shadow-[0_12px_32px_-8px_rgba(15,23,42,0.22),0_2px_6px_rgba(15,23,42,0.06)]"
    :style="style"
    data-testid="toc-turn-flyout"
  >
    <div class="mb-2 flex items-center gap-2">
      <span class="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-purple-100 text-[10px] font-bold tabular-nums text-purple-700">{{ item.idx + 1 }}</span>
      <span class="font-mono text-[11px] text-slate-400">{{ fmtTime(item.timestamp) }}</span>
      <span class="ml-auto rounded-md bg-slate-100 px-1.5 py-px text-[9.5px] font-bold uppercase tracking-[0.06em] text-slate-500">{{ expanded ? 'Expanded' : 'Collapsed' }}</span>
    </div>
    <div class="line-clamp-4 text-[12.5px] leading-[1.45] text-slate-700">{{ item.promptText }}</div>
    <div class="mt-2.5 grid grid-cols-2 gap-1.5">
      <div class="rounded-lg bg-slate-50 px-2 py-1.5">
        <div class="text-[9.5px] font-bold uppercase tracking-[0.06em] text-slate-400">Cost</div>
        <div class="text-[13px] font-bold tabular-nums text-emerald-600">{{ metrics.costUsd != null ? fmtCost(metrics.costUsd) : '—' }}</div>
      </div>
      <div class="rounded-lg bg-slate-50 px-2 py-1.5">
        <div class="text-[9.5px] font-bold uppercase tracking-[0.06em] text-slate-400">Duration</div>
        <div class="text-[13px] font-semibold tabular-nums text-slate-800">{{ item.durationMs ? fmtDuration(item.durationMs) : '—' }}</div>
      </div>
    </div>
    <ConversationTurnMeta :metrics="metrics" class="mt-2 text-[10.5px]" chip-class="rounded-md px-1.5 py-px" />
    <div class="mt-2 text-[10.5px] text-slate-400">click to jump to this turn</div>
  </div>
  </Teleport>
</template>
