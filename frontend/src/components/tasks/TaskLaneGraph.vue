<script setup>
// The Tasks tab as a graph: one lane per feature (and one for loose tasks),
// columns by topological depth, so every dependency runs left to right.
// Connection handles on each node let an editor add edges in Drawio fashion;
// clicking an existing edge removes it. Both actions save immediately.
//
// Lanes, their done/total, the chain count and the hidden-lane count are the
// server's (`/api/tasks/graph?lanes=1`); this view only places boxes.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import TaskEdgePicker from './TaskEdgePicker.vue'
import TaskLaneNode from './TaskLaneNode.vue'
import TaskRefusal from './TaskRefusal.vue'
import TaskSheets from './TaskSheets.vue'
import api from '../../api'
import { invalidatePipelineCounts } from '../../composables/usePipelineCounts.js'
import { GRAPH_FILTERS, useLaneGraph } from '../../composables/useTaskViews.js'
import { useTaskVerbs } from '../../composables/useTaskVerbs.js'
import { LANE_W, NODE_H, NODE_W, layoutLanes } from '../../utils/taskLaneLayout.js'

const props = defineProps({
  params: { type: Object, required: true },
  open: { type: Boolean, default: true },
  find: { type: String, default: '' },
})

const ZOOM_STEP = 0.2
const ZOOM_MIN = 0.4
const ZOOM_MAX = 1.6

const scope = computed(() => ({ params: props.params, open: props.open }))
const narrowed = computed(() => GRAPH_FILTERS.some(k => k !== 'project_id' && props.params[k]))
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

// The card under the pointer (or keyboard focus): its own edges light up and
// every other edge recedes, so one task's chain reads out of the tangle.
const hovered = ref(null)
const hoveredEdge = ref(null)

function nodeClass(node) {
  return [`tlg-node tlg-${tag(node)}`, { 'is-found': matches.value.has(node.id) }]
}

function edgeClass(edge) {
  const lit = hovered.value != null && (edge.from === hovered.value || edge.to === hovered.value)
  return {
    'is-blocking': edge.blocking,
    'is-settled': edge.settled && !edge.blocking,
    'is-lit': lit,
    'is-dim': hovered.value != null && !lit,
  }
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

const canWrite = api.getStoredUser()?.role !== 'viewer'
const sheet = ref(null)

async function afterWrite() {
  invalidatePipelineCounts()
  await graph.load()
}

const picker = ref(null)
const saving = ref(false)
const refusal = ref(null)

function openPicker(task, direction) {
  picker.value = { task, direction }
}

function closePicker() {
  picker.value = null
}

async function onPickerSaved() {
  closePicker()
  refusal.value = null
  await afterWrite()
}

async function removeEdge(edge) {
  if (saving.value) return
  saving.value = true
  refusal.value = null
  try {
    const data = await api.get(`/tasks/graph?root=${edge.to}&depth=1`)
    const deps = (data.edges || [])
      .filter(e => e.task_id === edge.to && e.depends_on_id !== edge.from)
      .map(e => ({ id: e.depends_on_id, kind: e.kind || 'requires' }))
    const result = await api.put(`/tasks/${edge.to}/deps`, { depends_on: deps })
    if (result.ok === false) {
      refusal.value = { message: result.msg || 'The server refused this change.', pair: result.body?.pair || [] }
      return
    }
    await afterWrite()
  } catch (e) {
    refusal.value = { message: e?.message || String(e), pair: [] }
  } finally {
    saving.value = false
  }
}

const { refusals, busyId, act } = useTaskVerbs(afterWrite, {
  openSheet: (kind, task) => { sheet.value = { kind, task } },
})
const refused = computed(() => Object.entries(refusals.value)
  .map(([id, refusal]) => ({ id: Number(id), refusal })))

async function sheetDone() {
  sheet.value = null
  await afterWrite()
}

defineExpose({ reload: graph.load })
</script>

<template>
  <div class="tlg" data-pl-part="canvas" data-testid="task-lane-graph">
    <p v-if="graph.error.value" class="tlg-note" role="alert">
      Could not read the graph — {{ graph.error.value }}
      <Button variant="link" size="sm" @click="graph.load">Retry</Button>
    </p>
    <p v-else-if="!graph.body.value" class="tlg-note">Loading the graph…</p>
    <p v-else-if="graph.body.value.truncated" class="tlg-note" role="status" data-testid="task-graph-truncated">
      {{ graph.body.value.total_nodes }} tasks is too many to draw. Keep “Open chains only”
      on, pick a project, or narrow the filters.
    </p>
    <p v-else-if="!layout.nodes.length" class="tlg-note" data-testid="task-graph-empty">
      <template v-if="narrowed && open">No open chains match these filters — turn off “Open chains only” to see finished tasks.</template>
      <template v-else-if="narrowed">No tasks match these filters.</template>
      <template v-else>{{ open ? 'No open chains to draw.' : 'No tasks to draw yet.' }}</template>
    </p>
    <template v-else>
      <ul v-if="refused.length" class="tlg-refusals">
        <li v-for="r in refused" :key="r.id" :data-testid="`graph-refusal-${r.id}`">
          <router-link :to="`/tasks/${r.id}`">#{{ r.id }}</router-link>
          <TaskRefusal :refusal="r.refusal" />
        </li>
      </ul>
      <div v-if="canWrite" class="tlg-chrome" :class="{ 'is-saving': saving }">
        <span v-if="saving" class="tlg-status" data-testid="graph-saving">Saving…</span>
        <span v-else-if="refusal" class="tlg-refusal" role="alert" data-testid="graph-refusal">
          {{ refusal.message }}
          <span v-if="refusal.pair.length === 2">({{ refusal.pair[0] }} → {{ refusal.pair[1] }})</span>
        </span>
        <span v-else class="tlg-hint" data-testid="graph-hint">
          Hover a task to connect; click an edge to remove it.
        </span>
      </div>
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
              <div class="tlg-lane-head" data-pl-part="lane-head" :style="{ width: `${LANE_W}px` }">
                <span class="tlg-lane-ref" data-pl-part="meta-label">{{ lanePrefix(lane) }}</span>
                <span class="tlg-lane-title" :title="lane.title">{{ lane.title }}</span>
                <span class="tlg-lane-bar" data-pl-part="meter" aria-hidden="true">
                  <span data-pl-part="meter-cell" :style="{ width: `${progress(lane)}%` }" />
                </span>
                <span class="tlg-lane-count" data-pl-part="meta-label" data-testid="graph-lane-count">{{ lane.done }} / {{ lane.total }}</span>
              </div>
            </div>
            <svg class="tlg-edges" :width="layout.width" :height="layout.height" aria-hidden="true">
              <defs>
                <marker
                  id="tlg-arrow"
                  viewBox="0 0 8 8"
                  refX="7"
                  refY="4"
                  markerWidth="7"
                  markerHeight="7"
                  markerUnits="userSpaceOnUse"
                  orient="auto"
                >
                  <path d="M1,1 L7,4 L1,7" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
                </marker>
              </defs>
              <path
                v-for="edge in layout.edges"
                :key="edge.key"
                :d="edge.path"
                class="tlg-edge"
                :class="edgeClass(edge)"
                marker-end="url(#tlg-arrow)"
                :data-testid="`graph-edge-${edge.key}`"
                :data-kind="edge.kind"
                :data-blocking="edge.blocking ? '' : undefined"
              />
              <path
                v-for="edge in layout.edges"
                :key="`hit-${edge.key}`"
                :d="edge.path"
                class="tlg-edge-hit"
                :data-testid="`graph-edge-hit-${edge.key}`"
                @mouseenter="hoveredEdge = edge.key"
                @mouseleave="hoveredEdge = null"
                @click="removeEdge(edge)"
              />
              <g
                v-for="edge in layout.edges"
                :key="`remove-${edge.key}`"
                class="tlg-edge-remove"
                :class="{ 'is-visible': hoveredEdge === edge.key && !saving }"
                :transform="`translate(${edge.midX}, ${edge.midY})`"
                :data-testid="`graph-edge-remove-${edge.key}`"
                @click="removeEdge(edge)"
              >
                <circle r="7" />
                <path d="M4,4 L-4,-4 M-4,4 L4,-4" />
              </g>
            </svg>
            <TaskLaneNode
              v-for="node in layout.nodes"
              :key="node.id"
              :node="node"
              :tag="tag(node)"
              :class="nodeClass(node)"
              :data-found="matches.has(node.id) ? '' : undefined"
              :style="{ left: `${node.x}px`, top: `${node.y}px`, width: `${NODE_W}px`, height: `${NODE_H}px` }"
              :busy="busyId === node.id"
              :can-write="canWrite"
              @connect="openPicker"
              @hover="id => (hovered = id)"
              @verb="act"
            />
          </div>
        </div>
      </div>
      <p class="tlg-footer" data-pl-part="meta-label" data-testid="graph-footer">{{ footer }}</p>
      <div class="tlg-zoom" role="group" aria-label="Zoom">
        <Button variant="secondary" size="icon" data-pl-part="key" aria-label="Zoom in" @click="setZoom(zoom + ZOOM_STEP)">
          <Icon name="plus" :size="14" />
        </Button>
        <Button variant="secondary" size="icon" data-pl-part="key" aria-label="Zoom out" @click="setZoom(zoom - ZOOM_STEP)">
          <Icon name="minus" :size="14" />
        </Button>
        <Button variant="secondary" size="icon" class="tlg-fit" data-pl-part="key" aria-label="Fit to width" @click="fit">FIT</Button>
      </div>
    </template>
    <TaskEdgePicker
      v-if="picker"
      :task="picker.task"
      :direction="picker.direction"
      :open="true"
      @close="closePicker"
      @saved="onPickerSaved"
    />
    <TaskSheets :sheet="sheet" @close="sheet = null" @done="sheetDone" />
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
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: opacity 120ms ease, stroke-width 120ms ease;
}
.tlg-edge.is-settled {
    stroke: var(--color-success);
    stroke-opacity: 0.55;
}
.tlg-edge.is-blocking {
    stroke: var(--color-warning);
    stroke-width: 1.75;
    stroke-dasharray: 5 4;
}
.tlg-edge.is-lit { stroke-width: 2.25; stroke-opacity: 1; }
.tlg-edge-hit {
    fill: none;
    stroke: transparent;
    stroke-width: 12;
    pointer-events: stroke;
    cursor: pointer;
}
.tlg-edge-remove {
    opacity: 0;
    pointer-events: none;
    cursor: pointer;
    transition: opacity 120ms ease;
}
.tlg-edge-remove.is-visible {
    opacity: 1;
    pointer-events: auto;
}
.tlg-edge-remove circle {
    fill: var(--color-surface);
    stroke: var(--color-danger);
    stroke-width: 1.5;
}
.tlg-edge-remove path {
    stroke: var(--color-danger);
    stroke-width: 1.5;
    stroke-linecap: round;
}
.tlg-node.is-found { box-shadow: 0 0 0 3px var(--color-ring); }
.tlg-chrome {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-height: 2.25rem;
    padding: 0.5rem 0.625rem;
    background: var(--color-surface);
    border-bottom: 1px solid var(--color-border);
}
.tlg-chrome.is-saving { background: var(--color-surface-2); }
.tlg-status {
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.tlg-refusal {
    font-size: 0.8125rem;
    color: var(--color-danger);
}
.tlg-hint {
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.tlg-edge.is-dim { opacity: 0.18; }
/* The status is a 3px stripe on the card's leading edge rather than a whole
   tinted border: colour marks the state without outlining every box. */
.tlg-node {
    --tlg-accent: var(--color-border-strong);
    position: absolute;
    display: flex;
    flex-direction: column;
    gap: 0.3125rem;
    padding: 0.5rem 0.625rem 0.5rem 0.8125rem;
    overflow: visible;
    color: var(--color-fg);
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    box-shadow: 0 1px 2px color-mix(in srgb, var(--color-fg) 6%, transparent);
    transition: border-color 120ms ease, box-shadow 120ms ease;
}
.tlg-node::before {
    content: '';
    position: absolute;
    inset: 0 auto 0 0;
    width: 3px;
    background: var(--tlg-accent);
}
.tlg-node:hover {
    text-decoration: none;
    border-color: var(--color-border-strong);
    box-shadow: 0 4px 12px color-mix(in srgb, var(--color-fg) 12%, transparent);
}
/* The contents fade, never the card: a translucent surface shows every edge behind it. */
.tlg-done > :deep(*) { opacity: 0.6; }
.tlg-done { --tlg-accent: var(--color-success); }
.tlg-done::before { opacity: 0.45; }
.tlg-armed { --tlg-accent: var(--color-info); }
.tlg-blocked { --tlg-accent: var(--color-warning); }
.tlg-failed { --tlg-accent: var(--color-danger); }
.tlg-running {
    --tlg-accent: var(--color-agent);
    border-color: var(--color-agent);
    box-shadow: 0 0 0 1px var(--color-agent), 0 0 0 4px var(--color-agent-soft);
}
.tlg-node :deep(.tlg-node-tag) {
    margin-left: auto;
    font-size: 0.6875rem;
    font-weight: 600;
    color: var(--color-fg-subtle);
}
.tlg-done :deep(.tlg-node-tag) { color: var(--color-success); }
.tlg-running :deep(.tlg-node-tag) { color: var(--color-agent); }
.tlg-blocked :deep(.tlg-node-tag) { color: var(--color-warning); }
.tlg-armed :deep(.tlg-node-tag) { color: var(--color-info); }
.tlg-failed :deep(.tlg-node-tag) { color: var(--color-danger); }
.tlg-refusals {
    margin: 0 0 0.5rem;
    padding: 0;
    list-style: none;
    font-size: 0.75rem;
}
.tlg-refusals li { display: flex; align-items: baseline; gap: 0.5rem; }
.tlg-refusals :deep(.task-refusal) { margin-top: 0; }
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
