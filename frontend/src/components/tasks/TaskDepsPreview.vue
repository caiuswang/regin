<script setup>
// The dependency editor's live picture: what this task will wait for above
// it, what already waits for it below. Clicking is a second way to edit the
// picked set — the list beside it stays the source of truth.
import { computed, ref } from 'vue'
import { NODE_H, NODE_W, layoutGraph } from '../../utils/taskGraphLayout.js'

const props = defineProps({
  subset: { type: Object, required: true },
  readonly: { type: Boolean, default: false },
})
const emit = defineEmits(['add', 'remove'])

const KIND_LABEL = { requires: 'requires', after: 'after' }
const DEPENDENT_HINT = 'A task cannot depend on its dependent.'

const layout = computed(() => layoutGraph(props.subset.nodes, props.subset.edges))
const hovered = ref(null)
const hint = ref('')

function roleOf(id) {
  return props.subset.roles.get(id) || 'ghost'
}

function edgeClass(edge) {
  const [to, from] = edge.key.split('-').map(Number)
  const lit = hovered.value != null && (to === hovered.value || from === hovered.value)
  return {
    'is-picked': roleOf(from) === 'picked',
    'is-lit': lit,
    'is-dim': hovered.value != null && !lit,
  }
}

function edgeFrom(edge) {
  return Number(edge.key.split('-')[1])
}

function clickNode(node) {
  if (props.readonly) return
  const role = roleOf(node.id)
  hint.value = role === 'dependent' ? DEPENDENT_HINT : ''
  if (role === 'ghost') emit('add', node.id)
  else if (role === 'picked') emit('remove', node.id)
}

function clickEdge(edge) {
  if (props.readonly) return
  const from = edgeFrom(edge)
  if (roleOf(from) === 'picked') emit('remove', from)
}

function nodeTitle(node) {
  return roleOf(node.id) === 'dependent' ? DEPENDENT_HINT : `#${node.id} ${node.title}`
}
</script>

<template>
  <div class="tdp" data-testid="task-deps-preview">
    <div class="tdp-scroll">
      <svg
        :width="layout.width"
        :height="layout.height"
        :viewBox="`0 0 ${layout.width} ${layout.height}`"
        role="group"
        aria-label="Dependency preview"
      >
        <defs>
          <marker id="tdp-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" markerUnits="userSpaceOnUse" orient="auto">
            <path d="M1,1 L7,4 L1,7" fill="none" stroke="context-stroke" stroke-width="1.5" />
          </marker>
        </defs>
        <g
          v-for="edge in layout.edges"
          :key="edge.key"
          class="tdp-edge"
          :class="edgeClass(edge)"
          :data-testid="`deps-preview-edge-${edge.key}`"
          :data-kind="edge.kind"
          @click="clickEdge(edge)"
        >
          <line :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2" class="tdp-edge-hit" />
          <line :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2" class="tdp-edge-line" marker-end="url(#tdp-arrow)" />
          <text :x="(edge.x1 + edge.x2) / 2 + 4" :y="(edge.y1 + edge.y2) / 2" class="tdp-edge-label">{{ KIND_LABEL[edge.kind] }}</text>
        </g>
        <g
          v-for="node in layout.nodes"
          :key="node.id"
          class="tdp-node"
          :class="`is-${roleOf(node.id)}`"
          :transform="`translate(${node.x}, ${node.y})`"
          :data-testid="`deps-preview-node-${node.id}`"
          :data-role="roleOf(node.id)"
          @click="clickNode(node)"
          @mouseenter="hovered = node.id"
          @mouseleave="hovered = null"
        >
          <title>{{ nodeTitle(node) }}</title>
          <rect :width="NODE_W" :height="NODE_H" rx="8" />
          <text x="10" y="18" class="tdp-node-id">#{{ node.id }} · {{ node.status }}</text>
          <text x="10" y="36" class="tdp-node-title">{{ node.label }}</text>
        </g>
      </svg>
    </div>
    <p v-if="subset.hidden" class="tdp-note" data-testid="deps-preview-hidden">
      +{{ subset.hidden }} more dependents not shown
    </p>
    <p v-if="hint" class="tdp-note tdp-hint" role="status" data-testid="deps-preview-hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.tdp {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
    min-width: 0;
}
.tdp-scroll {
    max-height: 22rem;
    overflow: auto;
    background-color: var(--color-surface);
    background-image: radial-gradient(var(--color-border) 1px, transparent 1px);
    background-size: 16px 16px;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
}
.tdp-edge { cursor: default; }
.tdp-edge.is-picked { cursor: pointer; }
.tdp-edge-hit {
    stroke: transparent;
    stroke-width: 12;
}
.tdp-edge-line {
    stroke: var(--color-border-strong);
    stroke-width: 1.5;
    transition: opacity 120ms ease, stroke-width 120ms ease;
}
.tdp-edge.is-picked .tdp-edge-line { stroke: var(--color-primary); }
.tdp-edge.is-lit .tdp-edge-line { stroke-width: 2.5; }
.tdp-edge.is-dim { opacity: 0.25; }
.tdp-edge-label {
    font-size: 10px;
    fill: var(--color-fg-muted);
}
.tdp-node { cursor: pointer; }
.tdp-node rect {
    fill: var(--color-surface);
    stroke: var(--color-border);
    stroke-width: 1;
}
.tdp-node.is-current rect {
    stroke: var(--color-fg);
    stroke-width: 2;
}
.tdp-node.is-picked rect {
    stroke: var(--color-primary);
    stroke-width: 2;
}
.tdp-node.is-ghost rect { stroke-dasharray: 4 3; }
.tdp-node.is-ghost,
.tdp-node.is-dependent { opacity: 0.7; }
.tdp-node.is-dependent { cursor: not-allowed; }
.tdp-node.is-current { cursor: default; }
.tdp-node-id {
    font-size: 10px;
    fill: var(--color-fg-subtle);
}
.tdp-node-title {
    font-size: 12px;
    fill: var(--color-fg);
}
.tdp-note {
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}
.tdp-hint { color: var(--color-warning); }
</style>
