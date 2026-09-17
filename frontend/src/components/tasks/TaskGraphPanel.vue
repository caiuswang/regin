<script setup>
// The task DAG, in plain SVG — no graph library for a picture that is a few
// boxes and straight lines. Layers run top to bottom, so every edge points
// downward: what a task waits for is drawn above it.
//
// Past ~8 nodes a layer grows wider than the column; the drawing keeps its
// intrinsic width and the wrapper scrolls sideways, rather than shrinking the
// labels until nothing is readable. Past the server's cap there is no drawing
// at all — the reader focuses on one task and reads its neighbourhood.
import { onMounted } from 'vue'
import Card from '../Card.vue'
import TaskGraphControls from './TaskGraphControls.vue'
import { NODE_H, NODE_W } from '../../utils/taskGraphLayout.js'
import { DRAW_NODE_CAP, useTaskGraph } from '../../composables/useTaskGraph.js'

const emit = defineEmits(['focus'])

const {
  nodes, loading, error, layout, load,
  root, depth, status, truncated, totalNodes,
  focus, setDepth, setStatus,
} = useTaskGraph({ cap: DRAW_NODE_CAP })

const HALO_PAD = 5

function nodeClass(node) {
  return ['tg-node', `tg-${node.status}`, {
    'tg-blocked': node.blocked,
    'tg-runnable': node.runnable,
    'tg-root': node.id === root.value,
  }]
}

function nodeHint(node) {
  const blocked = node.blocked ? ` — blocked by ${node.blocked_by.join(', ')}` : ''
  const runnable = node.runnable ? ' — runnable' : ''
  return `#${node.id} ${node.title} (${node.status})${blocked}${runnable}`
}

function pick(node) {
  emit('focus', node.id)
  focus(node.id)
}

onMounted(load)
</script>

<template>
  <Card>
    <div class="tg-head">
      <h2 class="card-header">Dependency graph</h2>
      <TaskGraphControls
        :root="root"
        :depth="depth"
        :status="status"
        @focus="focus"
        @update:depth="setDepth"
        @update:status="setStatus"
        @refresh="load"
      />
    </div>

    <div v-if="loading" class="empty-state">Loading the graph…</div>
    <div v-else-if="error" class="empty-state" role="alert" data-testid="task-graph-error">
      {{ error }}
    </div>
    <div v-else-if="truncated" class="tg-too-many" role="status" data-testid="task-graph-truncated">
      <p class="tg-too-many-head">{{ totalNodes }} tasks is too many to draw.</p>
      <p class="tg-too-many-hint">Focus on a task or filter by status.</p>
    </div>
    <div v-else-if="!nodes.length" class="empty-state" data-testid="task-graph-empty">
      <template v-if="root !== null || status">
        Nothing here to draw. Widen the depth, or clear the focus and the status filter.
      </template>
      <template v-else>No tasks to draw yet.</template>
    </div>
    <template v-else>
      <div class="tg-scroll" data-testid="task-graph">
        <svg
          class="tg-svg"
          :width="layout.width"
          :height="layout.height"
          :viewBox="`0 0 ${layout.width} ${layout.height}`"
          role="group"
          aria-label="Task dependency graph"
        >
          <line
            v-for="edge in layout.edges"
            :key="edge.key"
            class="tg-edge"
            :class="{ 'tg-edge-after': edge.kind === 'after' }"
            :data-kind="edge.kind"
            :data-testid="`task-graph-edge-${edge.key}`"
            :x1="edge.x1"
            :y1="edge.y1"
            :x2="edge.x2"
            :y2="edge.y2"
          />
          <g
            v-for="node in layout.nodes"
            :key="node.id"
            class="tg-group cursor-pointer focus-visible:outline-none"
            :data-testid="`task-graph-node-${node.id}`"
            :data-layer="node.layer"
            :data-root="node.id === root ? '' : undefined"
            role="button"
            tabindex="0"
            :aria-label="`Focus on ${nodeHint(node)}`"
            @click="pick(node)"
            @keydown.enter.prevent="pick(node)"
            @keydown.space.prevent="pick(node)"
          >
            <title>{{ nodeHint(node) }}</title>
            <rect
              v-if="node.id === root"
              class="tg-halo"
              :x="node.x - HALO_PAD"
              :y="node.y - HALO_PAD"
              :width="NODE_W + HALO_PAD * 2"
              :height="NODE_H + HALO_PAD * 2"
              rx="12"
            />
            <rect
              :class="nodeClass(node)"
              :x="node.x"
              :y="node.y"
              :width="NODE_W"
              :height="NODE_H"
              rx="8"
            />
            <text class="tg-title" :x="node.x + 10" :y="node.y + 19">{{ node.label }}</text>
            <text class="tg-sub" :x="node.x + 10" :y="node.y + 35">
              #{{ node.id }} · {{ node.status }}{{ node.blocked ? ' · blocked' : '' }}{{ node.runnable ? ' · runnable' : '' }}
            </text>
          </g>
        </svg>
      </div>
      <p class="tg-legend">
        Layers read top to bottom: a task sits below everything it waits for.
        A dashed outline is blocked; a solid green one is armed and runnable.
        A solid line is a real dependency and stays. A dashed line is ordering
        only: its task runs whether the one above it succeeded or failed, and
        the line is dropped once that has happened.
        Click a box — or press Enter on it — to re-root the graph there.
      </p>
    </template>
  </Card>
</template>

<style scoped>
.tg-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
}
.tg-scroll {
    max-width: 100%;
    overflow-x: auto;
    overflow-y: hidden;
}
.tg-svg { display: block; }
.tg-edge {
    stroke: var(--color-border-strong);
    stroke-width: 1.5;
}
.tg-edge-after {
    stroke: var(--color-border);
    stroke-dasharray: 5 4;
}
.tg-node {
    fill: var(--color-surface-2);
    stroke: var(--color-border);
    stroke-width: 1.5;
}
.tg-idea { fill: var(--color-surface-2); }
.tg-armed { fill: var(--color-info-soft); stroke: var(--color-info); }
.tg-queued { fill: var(--color-warning-soft); stroke: var(--color-warning); }
.tg-running { fill: var(--color-info-soft); stroke: var(--color-info-strong); }
.tg-done { fill: var(--color-success-soft); stroke: var(--color-success); }
.tg-failed { fill: var(--color-danger-soft); stroke: var(--color-danger); }
.tg-archived { fill: var(--color-surface-3); }
.tg-blocked { stroke-dasharray: 4 3; }
.tg-runnable { stroke: var(--color-success); stroke-width: 2.5; }
.tg-root {
    stroke: var(--color-primary);
    stroke-width: 3;
}
.tg-halo {
    fill: none;
    stroke: var(--color-primary);
    stroke-width: 1.5;
    opacity: 0.4;
}
.tg-group:hover .tg-node { stroke: var(--color-primary-hover); }
.tg-group:focus-visible .tg-node {
    stroke: var(--color-ring);
    stroke-width: 3;
}
.tg-title {
    font-size: 12px;
    font-weight: 600;
    fill: var(--color-fg);
}
.tg-sub {
    font-size: 10px;
    fill: var(--color-fg-muted);
}
.tg-too-many {
    padding: 1.25rem;
    text-align: center;
    background: var(--color-surface-2);
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
}
.tg-too-many-head {
    font-size: 0.875rem;
    font-weight: 600;
    color: var(--color-fg);
}
.tg-too-many-hint {
    margin-top: 0.25rem;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.tg-legend {
    margin-top: 0.75rem;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
}
</style>
