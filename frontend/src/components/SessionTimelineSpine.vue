<script setup>
// Timeline "event spine" view mode: a chronological, narrative rendering of the
// span tree that replaces the PrimeVue TreeTable. `treeNodes` is the same
// client-built hierarchy the tree consumed (root + lazily-loaded children); we
// flatten it in pre-order, honoring `expandedKeys` so a collapsed node hides its
// subtree, and render each node as a TimelineSpineRow. Selection + lazy child
// loading stay with the parent: this only emits `node-select` (payload
// `{ node }`) / `toggle-node` (the node), the same contract the parent's
// `onNodeSelect` / `toggleTimelineNode` handlers already expect.
import { computed } from 'vue'
import { fmtDuration } from '../utils/traceFormatters.js'
import { runStateOf } from '../utils/liveRows.js'
import TimelineSpineRow from './TimelineSpineRow.vue'

const props = defineProps({
  treeNodes: { type: Array, default: () => [] },
  expandedKeys: { type: Object, default: () => ({}) },
  selectedKeys: { type: Object, default: () => ({}) },
  // Trace start in epoch ms + total duration, for the per-row t+offset and the
  // section header readout.
  traceStart: { type: Number, default: 0 },
  traceDuration: { type: Number, default: 0 },
  showCost: { type: Boolean, default: true },
})
const emit = defineEmits(['node-select', 'toggle-node'])

// The strongest in-flight state anywhere in a node's LOADED subtree. A
// collapsed node otherwise hides the only row that is still moving — the
// marker is worth least exactly when the reader cannot see the row it is on.
// `waiting` outranks `running`: one of them needs the reader, the other does
// not. Unloaded children yield nothing, which is honest — the tree cannot
// claim a state for spans it has never seen.
function subtreeRunState(node) {
  let found = ''
  for (const child of node?.children || []) {
    const own = runStateOf(child?.data) || subtreeRunState(child)
    // Not an early return on the first hit: a `waiting` anywhere below must
    // beat a `running` found earlier in the walk, so only `waiting` short-
    // circuits.
    if (own === 'waiting') return 'waiting'
    if (own) found = own
  }
  return found
}

const rows = computed(() => {
  const out = []
  const walk = (nodes, depth) => {
    for (const n of nodes || []) {
      if (!n?.key || !n?.data?.span_id) continue
      const open = props.expandedKeys[n.key] && n.children?.length
      out.push({ node: n, depth, foldedRunState: open ? '' : subtreeRunState(n) })
      if (open) walk(n.children, depth + 1)
    }
  }
  walk(props.treeNodes, 0)
  return out
})

// Longest root duration normalizes every row's mini duration bar so they share
// one scale (a 4-minute agent reads as full-width, a 200ms edit as a sliver).
const rootMaxMs = computed(() =>
  (props.treeNodes || []).reduce((max, n) => Math.max(max, n?.data?.duration_ms || 0), 0) || 1)

const durTxt = computed(() => fmtDuration(props.traceDuration) || '—')

function onSelect(node) { emit('node-select', { node }) }
function onToggle(node) { emit('toggle-node', node) }
</script>

<template>
  <div class="px-4 py-4 sm:px-5">
    <div class="mb-1.5 flex items-center gap-2">
      <span class="text-[10px] font-bold uppercase tracking-widest text-slate-400">Timeline</span>
      <span class="h-px flex-1 bg-slate-200"></span>
      <span class="font-mono text-[10px] text-slate-400">{{ durTxt }} · {{ rows.length }} events</span>
    </div>

    <TimelineSpineRow
      v-for="r in rows"
      :key="r.node.data.span_id"
      :span="r.node.data"
      :depth="r.depth"
      :selected="!!selectedKeys[r.node.key]"
      :expanded="!!expandedKeys[r.node.key]"
      :has-kids="!r.node.leaf"
      :child-count="r.node.data.child_count || 0"
      :folded-run-state="r.foldedRunState"
      :root-max-ms="rootMaxMs"
      :trace-start="traceStart"
      :show-cost="showCost"
      @select="onSelect(r.node)"
      @toggle="onToggle(r.node)"
    />

    <div class="flex justify-center pt-5 pb-1">
      <span class="font-mono text-[10px] uppercase tracking-widest text-slate-300">End of timeline</span>
    </div>
  </div>
</template>
