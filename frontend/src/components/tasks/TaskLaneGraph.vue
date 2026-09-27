<script setup>
// The Tasks tab as a graph: one lane per feature (and one for loose tasks),
// columns by topological depth, so every dependency runs left to right. A
// blocking edge — one whose dependency has not released its task — is amber
// and dashed; an edge out of a finished task is green.
//
// Lanes, their done/total, the chain count and the hidden-lane count are the
// server's (`/api/tasks/graph?lanes=1`); this view only places boxes.
import { computed, nextTick, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import TaskStatusIcon from './TaskStatusIcon.vue'
import { useLaneGraph } from '../../composables/useTaskViews.js'
import { LANE_W, NODE_H, NODE_W, layoutLanes } from '../../utils/taskLaneLayout.js'

const props = defineProps({
  project: { type: String, default: '' },
  open: { type: Boolean, default: true },
  find: { type: String, default: '' },
})

const ZOOM_STEP = 0.2
const ZOOM_MIN = 0.4
const ZOOM_MAX = 1.6

const scope = computed(() => ({ project: props.project, open: props.open }))
const graph = useLaneGraph(scope)
const zoom = ref(1)
const viewport = ref(null)

const layout = computed(() => {
  const b = graph.body.value
  return b ? layoutLanes(b.lanes, b.nodes, b.edges) : null
})

const needle = computed(() => props.find.trim().replace(/^#/, '').toLowerCase())
const matches = computed(() => {
  if (!needle.value || !layout.value) return new Set()
  return new Set(layout.value.nodes
    .filter(n => String(n.id) === needle.value || n.title.toLowerCase().includes(needle.value))
    .map(n => n.id))
})

function tag(node) {
  return node.blocked && node.status !== 'running' ? 'blocked' : node.status
}

function nodeClass(node) {
  return [`tlg-node tlg-${tag(node)}`, { 'is-found': matches.value.has(node.id) }]
}

function progress(lane) {
  return lane.total ? Math.round((lane.done / lane.total) * 100) : 0
}

function lanePrefix(lane) {
  return lane.feature_id == null ? 'no feature' : `#${lane.feature_id}`
}

function setZoom(value) {
  zoom.value = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(value * 10) / 10))
}

function fit() {
  const el = viewport.value
  if (!el || !layout.value?.width) return
  setZoom(Math.min(1, el.clientWidth / layout.value.width))
}

async function reveal() {
  const first = layout.value?.nodes.find(n => matches.value.has(n.id))
  await nextTick()
  const el = viewport.value
  if (!first || !el) return
  el.scrollLeft = Math.max(0, first.x * zoom.value - el.clientWidth / 2)
  el.scrollTop = Math.max(0, first.y * zoom.value - el.clientHeight / 2)
}

watch(() => props.find, reveal)
watch(scope, () => graph.load(), { immediate: true })

const footer = computed(() => {
  const b = graph.body.value
  if (!b) return ''
  const chains = `${b.chains} ${b.chains === 1 ? 'chain' : 'chains'}`
  if (!b.open_only) return chains
  const n = b.lanes_hidden
  return `${chains} · ${n} feature ${n === 1 ? 'lane' : 'lanes'} hidden (no open tasks)`
})

defineExpose({ reload: graph.load })
</script>

<template>
  <div class="tlg" data-testid="task-lane-graph">
    <p v-if="graph.error.value" class="tlg-note" role="alert">
      Could not read the graph — {{ graph.error.value }}
      <Button variant="link" size="sm" @click="graph.load">Retry</Button>
    </p>
    <p v-else-if="!graph.body.value" class="tlg-note">Loading the graph…</p>
    <p v-else-if="graph.body.value.truncated" class="tlg-note" role="status" data-testid="task-graph-truncated">
      {{ graph.body.value.total_nodes }} tasks is too many to draw. Keep “Open chains only”
      on, or pick a project.
    </p>
    <p v-else-if="!layout.nodes.length" class="tlg-note" data-testid="task-graph-empty">
      {{ open ? 'No open chains to draw.' : 'No tasks to draw yet.' }}
    </p>
    <template v-else>
      <div ref="viewport" class="tlg-viewport">
        <div
          class="tlg-canvas"
          :style="{ width: `${layout.width * zoom}px`, height: `${layout.height * zoom}px` }"
        >
          <div
            class="tlg-stage"
            :style="{ width: `${layout.width}px`, height: `${layout.height}px`, transform: `scale(${zoom})` }"
          >
            <div
              v-for="lane in layout.lanes"
              :key="lane.key"
              class="tlg-lane"
              :style="{ top: `${lane.top}px`, height: `${lane.height}px` }"
              :data-testid="`graph-lane-${lane.key}`"
            >
              <div class="tlg-lane-head" :style="{ width: `${LANE_W}px` }">
                <span class="tlg-lane-ref">{{ lanePrefix(lane) }}</span>
                <span class="tlg-lane-title" :title="lane.title">{{ lane.title }}</span>
                <span class="tlg-lane-bar" aria-hidden="true">
                  <span :style="{ width: `${progress(lane)}%` }" />
                </span>
                <span class="tlg-lane-count" data-testid="graph-lane-count">{{ lane.done }} / {{ lane.total }}</span>
              </div>
            </div>
            <svg class="tlg-edges" :width="layout.width" :height="layout.height" aria-hidden="true">
              <path
                v-for="edge in layout.edges"
                :key="edge.key"
                :d="edge.path"
                class="tlg-edge"
                :class="{ 'is-blocking': edge.blocking, 'is-settled': edge.settled && !edge.blocking }"
                :data-testid="`graph-edge-${edge.key}`"
                :data-kind="edge.kind"
                :data-blocking="edge.blocking ? '' : undefined"
              />
            </svg>
            <router-link
              v-for="node in layout.nodes"
              :key="node.id"
              :to="`/tasks/${node.id}`"
              :class="nodeClass(node)"
              :style="{ left: `${node.x}px`, top: `${node.y}px`, width: `${NODE_W}px`, height: `${NODE_H}px` }"
              :data-testid="`graph-node-${node.id}`"
              :data-column="node.column"
            >
              <span class="tlg-node-top">
                <TaskStatusIcon :status="node.status" :size="12" />
                <span class="tlg-node-id">#{{ node.id }}</span>
                <span class="tlg-node-tag">{{ tag(node) }}</span>
              </span>
              <span class="tlg-node-title">{{ node.title }}</span>
            </router-link>
          </div>
        </div>
      </div>
      <p class="tlg-footer" data-testid="graph-footer">{{ footer }}</p>
      <div class="tlg-zoom" role="group" aria-label="Zoom">
        <Button variant="secondary" size="icon" aria-label="Zoom in" @click="setZoom(zoom + ZOOM_STEP)">
          <Icon name="plus" :size="14" />
        </Button>
        <Button variant="secondary" size="icon" aria-label="Zoom out" @click="setZoom(zoom - ZOOM_STEP)">
          <Icon name="minus" :size="14" />
        </Button>
        <Button variant="secondary" size="icon" class="tlg-fit" aria-label="Fit to width" @click="fit">FIT</Button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.tlg {
    position: relative;
    overflow: hidden;
    background-color: var(--color-surface);
    background-image: radial-gradient(var(--color-border) 1px, transparent 1px);
    background-size: 16px 16px;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
}
.tlg-note {
    padding: 1.25rem 1rem;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
    background: var(--color-surface);
}
.tlg-viewport {
    max-height: 70vh;
    padding-bottom: 3rem;
    overflow: auto;
}
.tlg-canvas { position: relative; }
.tlg-stage {
    position: absolute;
    top: 0;
    left: 0;
    transform-origin: 0 0;
}
.tlg-lane {
    position: absolute;
    left: 0;
    right: 0;
    border-bottom: 1px solid var(--color-border);
}
.tlg-lane-head {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    height: 100%;
    padding: 1rem 1.25rem 0.875rem;
    background: var(--color-surface);
    border-right: 1px solid var(--color-border);
}
.tlg-lane-ref {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.6875rem;
    color: var(--color-fg-subtle);
}
.tlg-lane-title {
    display: -webkit-box;
    overflow: hidden;
    font-size: 0.875rem;
    font-weight: 600;
    line-height: 1.3;
    color: var(--color-fg);
    overflow-wrap: anywhere;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
}
.tlg-lane-bar {
    display: block;
    height: 0.25rem;
    margin-top: auto;
    overflow: hidden;
    background: var(--color-surface-3);
    border-radius: 999px;
}
.tlg-lane-bar > span {
    display: block;
    height: 100%;
    background: var(--color-success);
}
.tlg-lane-count {
    align-self: flex-end;
    font-size: 0.6875rem;
    color: var(--color-fg-muted);
    font-variant-numeric: tabular-nums;
}
.tlg-edges {
    position: absolute;
    top: 0;
    left: 0;
    overflow: visible;
    pointer-events: none;
}
.tlg-edge {
    fill: none;
    stroke: var(--color-border-strong);
    stroke-width: 1.5;
}
.tlg-edge.is-settled { stroke: var(--color-success); }
.tlg-edge.is-blocking {
    stroke: var(--color-warning);
    stroke-width: 2;
    stroke-dasharray: 5 4;
}
.tlg-node {
    position: absolute;
    display: flex;
    flex-direction: column;
    gap: 0.3125rem;
    padding: 0.5rem 0.625rem;
    color: var(--color-fg);
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
}
.tlg-node:hover { text-decoration: none; border-color: var(--color-border-strong); }
.tlg-node:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
.tlg-done { opacity: 0.6; }
.tlg-running { border-color: var(--color-agent); box-shadow: 0 0 0 1px var(--color-agent); }
.tlg-blocked { border-color: var(--color-warning); }
.tlg-node.is-found { box-shadow: 0 0 0 3px var(--color-ring); }
.tlg-node-top {
    display: flex;
    align-items: center;
    gap: 0.375rem;
}
.tlg-node-id {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 0.6875rem;
    color: var(--color-fg-subtle);
}
.tlg-node-tag {
    margin-left: auto;
    font-size: 0.6875rem;
    font-weight: 600;
    color: var(--color-fg-subtle);
}
.tlg-done .tlg-node-tag { color: var(--color-success); }
.tlg-running .tlg-node-tag { color: var(--color-agent); }
.tlg-queued .tlg-node-tag,
.tlg-blocked .tlg-node-tag { color: var(--color-warning); }
.tlg-armed .tlg-node-tag { color: var(--color-info); }
.tlg-failed .tlg-node-tag { color: var(--color-danger); }
.tlg-node-title {
    display: -webkit-box;
    overflow: hidden;
    font-size: 0.8125rem;
    font-weight: 500;
    line-height: 1.3;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
}
.tlg-footer {
    position: absolute;
    bottom: 0.625rem;
    left: 0.625rem;
    padding: 0.1875rem 0.625rem;
    font-size: 0.75rem;
    color: var(--color-fg-muted);
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
}
.tlg-zoom {
    position: absolute;
    right: 0.625rem;
    bottom: 0.625rem;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
}
.tlg .tlg-fit {
    font-size: 0.625rem;
    font-weight: 700;
}
</style>
