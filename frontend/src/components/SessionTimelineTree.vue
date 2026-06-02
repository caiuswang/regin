<script setup>
// Timeline (tree) view mode, extracted from SessionTraceView so all three
// view modes (Conversation / Terminal / Timeline) are sibling components and
// the deep TreeTable nesting lives outside the orchestrator.
//
// The parent owns selection + lazy child-loading: this renders the tree and
// emits `node-select` / `toggle-node`, and forwards the TreeTable's
// expanded/selection key v-models back up.
import { computed } from 'vue'
import TreeTable from 'primevue/treetable'
import Column from 'primevue/column'
import TreeIndent from './TreeIndent.vue'
import { spanLabel, mcpParts, fmtTokens, fmtCost } from '../utils/traceFormatters.js'
import { barColor } from '../utils/spanColors.js'

const props = defineProps({
  treeNodes: { type: Array, default: () => [] },
  expandedKeys: { type: Object, default: () => ({}) },
  selectedKeys: { type: Object, default: () => ({}) },
})

defineEmits(['node-select', 'toggle-node', 'update:expandedKeys', 'update:selectionKeys'])

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

// Depth per node key, so TreeIndent can render guide rails (PrimeVue's lazy
// TreeTable doesn't expose row depth in the body slot).
const nodeDepthByKey = computed(() => {
  const depth = new Map()
  function walk(nodes, level) {
    for (const n of nodes || []) {
      if (n?.key) depth.set(n.key, level)
      walk(n.children || [], level + 1)
    }
  }
  walk(props.treeNodes, 0)
  return depth
})
function depthForNode(node) {
  if (!node?.key) return 0
  return nodeDepthByKey.value.get(node.key) || 0
}

function hasToolTokens(d) {
  return d && (d.input_tokens != null || d.output_tokens != null)
}
function tokenTitle(d) {
  if (!d) return ''
  const parts = []
  if (d.input_tokens != null) parts.push(`in: ${d.input_tokens} (result on next turn)`)
  if (d.image_tokens) parts.push(`  image: ${d.image_tokens}`)
  if (d.output_tokens != null) parts.push(`out: ${d.output_tokens} (this turn's tool_use)`)
  if (d.cost_usd != null) parts.push(`cost: ${fmtCost(d.cost_usd)}`)
  return parts.join('\n')
}
</script>

<template>
  <TreeTable
    :value="treeNodes"
    :lazy="true"
    :expanded-keys="expandedKeys"
    :selection-keys="selectedKeys"
    selection-mode="single"
    class="text-sm"
    table-class="w-full table-fixed"
    @update:expanded-keys="$emit('update:expandedKeys', $event)"
    @update:selection-keys="$emit('update:selectionKeys', $event)"
    @node-select="$emit('node-select', $event)"
  >
    <Column field="name" header="Span" style="min-width: 14rem">
      <template #body="{ node }">
        <div class="flex items-center gap-2 min-w-0 w-full" :data-span-id="node.data.span_id">
          <TreeIndent
            :depth="depthForNode(node)"
            :leaf="node.leaf"
            :expanded="!!expandedKeys[node.key]"
            @toggle="$emit('toggle-node', node)"
          />
          <span
            class="inline-block rounded-full shrink-0 w-1.5 h-1.5"
            :class="barColor(node.data.name)"
          ></span>
          <div class="min-w-0 flex-1">
            <div class="font-medium truncate flex items-center gap-1" :title="spanLabel(node.data)">
              <span
                v-if="mcpParts(node.data.name)"
                class="inline-block text-[9px] font-semibold uppercase tracking-wider px-1 py-px rounded bg-cyan-100 text-cyan-800 shrink-0"
              >MCP</span>
              <span class="truncate">{{ spanLabel(node.data) }}</span>
            </div>
            <div class="text-xs text-gray-400 truncate" :title="node.data.name">{{ node.data.name }}</div>
          </div>
        </div>
      </template>
    </Column>

    <Column field="duration" header="Time" style="min-width: 5rem; width: 5rem">
      <template #body="{ node }">
        <div class="text-right text-xs text-gray-400">
          {{ fmtDuration(node.data.duration_ms) }}
        </div>
      </template>
    </Column>

    <Column field="tokens" header="Tokens" style="min-width: 7rem; width: 7rem">
      <template #body="{ node }">
        <div
          v-if="hasToolTokens(node.data)"
          class="text-right text-xs font-mono text-gray-500"
          :title="tokenTitle(node.data)"
        >
          <span class="text-gray-700">{{ fmtTokens(node.data.input_tokens) }}</span>
          <span class="text-gray-300 mx-1">/</span>
          <span>{{ fmtTokens(node.data.output_tokens) }}</span>
        </div>
      </template>
    </Column>
  </TreeTable>
</template>
