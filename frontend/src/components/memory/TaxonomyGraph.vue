<script setup>
// Graph view of the taxonomy: a DETERMINISTIC radial tidy-tree (no physics
// sim, no Math.random) over the parent_id structure, with cross-topic edges[]
// drawn as dashed chords. Node area ∝ sqrt(mem_count) (avoid lie-factor).
// Pan = pointer drag, zoom = wheel / buttons, applied via a <g> transform.
// The tree view is the primary accessible path; nodes here are still
// focusable buttons (Enter/Space select) as a supplementary affordance.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  roots: { type: Array, default: () => [] },
  nodes: { type: Object, default: () => ({}) },
  selectedId: { type: String, default: null },
  filter: { type: String, default: '' },
})
const emit = defineEmits(['select'])

const RING = 120 // px between depth rings
const kids = (id) => props.nodes[id]?.children || []

// --- Radial layout: angular span ∝ leaf count, radius ∝ depth -------------
function buildLayout() {
  const leaves = {}
  const countLeaves = (id) => {
    const k = kids(id)
    if (!k.length) return (leaves[id] = 1)
    return (leaves[id] = k.reduce((s, c) => s + countLeaves(c), 0))
  }
  let total = 0
  for (const r of props.roots) total += countLeaves(r)
  const pos = {}
  const place = (id, depth, a0, a1) => {
    const angle = (a0 + a1) / 2
    const radius = (depth + 1) * RING
    pos[id] = { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, depth, angle }
    const k = kids(id)
    let a = a0
    for (const c of k) {
      const next = a + (a1 - a0) * ((leaves[c] || 1) / (leaves[id] || 1))
      place(c, depth + 1, a, next)
      a = next
    }
  }
  let a = -Math.PI / 2
  for (const r of props.roots) {
    const next = a + Math.PI * 2 * ((leaves[r] || 1) / (total || 1))
    place(r, 0, a, next)
    a = next
  }
  return pos
}
const positions = computed(() => (props.roots.length ? buildLayout() : {}))

const q = computed(() => props.filter.trim().toLowerCase())
const isHit = (n) => !q.value || `${n.label || ''} ${n.blurb || ''}`.toLowerCase().includes(q.value)

const nodeList = computed(() => Object.entries(positions.value).map(([id, p]) => {
  const n = props.nodes[id] || {}
  const label = n.label || id
  return {
    id, x: p.x, y: p.y, angle: p.angle, depth: p.depth, label,
    r: 6 + Math.sqrt(n.mem_count || 0) * 2.4,
    bucket: p.depth === 0,
    short: label.length > 22 ? label.slice(0, 21) + '…' : label,
    anchor: Math.cos(p.angle) >= 0 ? 'start' : 'end',
    dim: !isHit(n),
  }
}))

const links = computed(() => {
  const out = []
  const P = positions.value
  // center hub → roots, and tree parent → child
  for (const r of props.roots) if (P[r]) out.push({ x1: 0, y1: 0, x2: P[r].x, y2: P[r].y, cross: false })
  for (const id in P) for (const c of kids(id)) if (P[c]) out.push({ x1: P[id].x, y1: P[id].y, x2: P[c].x, y2: P[c].y, cross: false })
  // cross edges (deduped)
  const seen = new Set()
  for (const id in P) for (const e of props.nodes[id]?.edges || []) {
    const t = e.target
    if (!P[t]) continue
    const key = id < t ? `${id}|${t}` : `${t}|${id}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push({ x1: P[id].x, y1: P[id].y, x2: P[t].x, y2: P[t].y, cross: true })
  }
  return out
})

// --- Pan / zoom -----------------------------------------------------------
const wrap = ref(null)
const dims = ref({ w: 800, h: 520 })
const tx = ref(400)
const ty = ref(260)
const s = ref(0.8)
const dragging = ref(false)
let moved = false
let startX = 0, startY = 0

function fit() {
  const maxR = nodeList.value.reduce((m, n) => Math.max(m, Math.hypot(n.x, n.y) + n.r + 60), RING)
  const { w, h } = dims.value
  s.value = Math.min(1.4, Math.max(0.25, Math.min(w, h) / (2 * maxR)))
  tx.value = w / 2
  ty.value = h / 2
}
function clampS(v) { return Math.min(3, Math.max(0.25, v)) }
function zoomAround(px, py, factor) {
  const wx = (px - tx.value) / s.value
  const wy = (py - ty.value) / s.value
  const ns = clampS(s.value * factor)
  tx.value = px - wx * ns
  ty.value = py - wy * ns
  s.value = ns
}
function onWheel(e) {
  e.preventDefault()
  const rect = wrap.value.getBoundingClientRect()
  zoomAround(e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 1.12 : 0.89)
}
function zoomBtn(factor) { zoomAround(dims.value.w / 2, dims.value.h / 2, factor) }
function onDown(e) { dragging.value = true; moved = false; startX = e.clientX; startY = e.clientY }
function onMove(e) {
  if (!dragging.value) return
  if (Math.abs(e.clientX - startX) + Math.abs(e.clientY - startY) > 3) moved = true
  tx.value += e.movementX
  ty.value += e.movementY
}
function onUp() { dragging.value = false }
function onNode(id) { if (!moved) emit('select', id) }
function onNodeKey(e, id) {
  if (e.key !== 'Enter' && e.key !== ' ') return
  e.preventDefault(); emit('select', id)
}

let ro
onMounted(() => {
  ro = new ResizeObserver(([entry]) => {
    dims.value = { w: entry.contentRect.width, h: entry.contentRect.height }
    fit()
  })
  ro.observe(wrap.value)
})
onBeforeUnmount(() => ro?.disconnect())
watch(() => props.roots, fit)
</script>

<template>
  <div
    ref="wrap"
    class="relative h-full w-full overflow-hidden rounded-lg border border-border bg-surface"
  >
    <svg
      class="h-full w-full touch-none select-none"
      :class="dragging ? 'cursor-grabbing' : 'cursor-grab'"
      role="application"
      aria-label="Topic taxonomy graph — drag to pan, scroll to zoom"
      @wheel="onWheel"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="onUp"
      @pointerleave="onUp"
    >
      <g :transform="`translate(${tx},${ty}) scale(${s})`">
        <!-- links -->
        <line
          v-for="(l, i) in links"
          :key="i"
          :x1="l.x1" :y1="l.y1" :x2="l.x2" :y2="l.y2"
          :stroke="l.cross ? 'var(--color-fg-faint)' : 'var(--color-border-strong)'"
          :stroke-dasharray="l.cross ? '4 4' : undefined"
          :stroke-width="l.cross ? 1 : 1.5"
          fill="none"
        />
        <!-- center hub -->
        <circle cx="0" cy="0" r="5" fill="var(--color-fg-muted)" />

        <!-- nodes -->
        <g
          v-for="n in nodeList"
          :key="n.id"
          class="tg-node cursor-pointer hover:[&>circle]:stroke-primary focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-2"
          role="button"
          tabindex="0"
          :aria-label="`${n.label}${selectedId === n.id ? ' (selected)' : ''}`"
          :opacity="n.dim ? 0.25 : 1"
          @click.stop="onNode(n.id)"
          @keydown="onNodeKey($event, n.id)"
        >
          <circle
            :cx="n.x" :cy="n.y" :r="n.r"
            :fill="n.bucket ? 'var(--color-primary)' : 'var(--color-surface)'"
            :stroke="selectedId === n.id ? 'var(--color-primary)' : 'var(--color-border-strong)'"
            :stroke-width="selectedId === n.id ? 3 : 1.5"
          />
          <text
            :x="n.x + (n.anchor === 'start' ? n.r + 4 : -(n.r + 4))"
            :y="n.y + 3"
            :text-anchor="n.anchor"
            :font-weight="n.bucket || selectedId === n.id ? 600 : 400"
            font-size="10"
            fill="var(--color-fg)"
          >{{ n.short }}</text>
        </g>
      </g>
    </svg>

    <p class="pointer-events-none absolute top-2 left-3 text-[11px] text-fg-faint">drag to pan · scroll to zoom</p>
    <div class="absolute bottom-2 right-2 flex flex-col gap-1">
      <Button variant="secondary" size="icon" class="h-7 w-7 focus-visible:outline-2 focus-visible:outline-ring" aria-label="Zoom in" @click="zoomBtn(1.2)"><Icon name="plus" :size="15" /></Button>
      <Button variant="secondary" size="icon" class="h-7 w-7 focus-visible:outline-2 focus-visible:outline-ring" aria-label="Zoom out" @click="zoomBtn(0.83)"><span class="text-base leading-none">−</span></Button>
      <Button variant="secondary" size="icon" class="h-7 w-7 focus-visible:outline-2 focus-visible:outline-ring" aria-label="Fit to view" @click="fit"><Icon name="search" :size="14" /></Button>
    </div>
  </div>
</template>

<style scoped>
.tg-node { cursor: pointer; }
.tg-node:focus-visible { outline: none; }
.tg-node:focus-visible circle { stroke: var(--color-ring); stroke-width: 3; }
</style>
