<script setup>
// Left rail: the parent_id tree as an accessible, keyboard-navigable tree
// (role=tree/treeitem, aria-level/expanded/selected, roving tabindex + arrow
// keys). Depth is shown with vertical guide rails; the selected row carries a
// left accent rail. A `filter` (owned by the parent) prunes to matches + their
// ancestors, force-expands the path, and highlights the matched substring.
import { computed, nextTick, ref, watch } from 'vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  roots: { type: Array, default: () => [] },
  nodes: { type: Object, default: () => ({}) },
  selectedId: { type: String, default: null },
  filter: { type: String, default: '' },
})
const emit = defineEmits(['select'])

const expanded = ref(new Set())
watch(() => props.roots, (r) => { expanded.value = new Set(r) }, { immediate: true })

// child → parent, derived from the authoritative children[] arrays.
const parentOf = computed(() => {
  const p = {}
  for (const id in props.nodes) for (const c of props.nodes[id].children || []) p[c] = id
  return p
})
function ancestorsOf(id) {
  const out = []
  let cur = parentOf.value[id]
  while (cur) { out.unshift(cur); cur = parentOf.value[cur] }
  return out
}

const q = computed(() => props.filter.trim().toLowerCase())

// When filtering: keep = matches ∪ ancestors-of-matches; force the path open.
const keep = computed(() => {
  if (!q.value) return null
  const set = new Set()
  for (const id in props.nodes) {
    const n = props.nodes[id]
    const hay = `${n.label || ''} ${n.blurb || ''}`.toLowerCase()
    if (hay.includes(q.value)) { set.add(id); ancestorsOf(id).forEach((a) => set.add(a)) }
  }
  return set
})

// Flatten the (visible) tree into an indented row list — depth ≤4, so a plain
// walk beats a recursive component.
const rows = computed(() => {
  const out = []
  const filtering = !!q.value
  const walk = (id, depth) => {
    const n = props.nodes[id]
    if (!n) return
    if (filtering && !keep.value.has(id)) return
    const kids = n.children || []
    const isOpen = filtering ? true : expanded.value.has(id)
    out.push({ id, depth, node: n, hasChildren: kids.length > 0, open: isOpen })
    if (isOpen) kids.forEach((c) => walk(c, depth + 1))
  }
  props.roots.forEach((r) => walk(r, 0))
  return out
})

// Split a label around the matched substring for highlighting.
function segments(label) {
  const f = q.value
  if (!f) return [{ t: label, hit: false }]
  const lower = (label || '').toLowerCase()
  const i = lower.indexOf(f)
  if (i < 0) return [{ t: label, hit: false }]
  return [
    { t: label.slice(0, i), hit: false },
    { t: label.slice(i, i + f.length), hit: true },
    { t: label.slice(i + f.length), hit: false },
  ].filter((s) => s.t)
}

function toggle(id) {
  if (q.value) return // expansion is derived while filtering
  const next = new Set(expanded.value)
  next.has(id) ? next.delete(id) : next.add(id)
  expanded.value = next
}
function onRow(row) {
  if (row.hasChildren) toggle(row.id)
  emit('select', row.id)
}

// --- Roving tabindex + arrow-key navigation -------------------------------
const focusId = ref(null)
watch([() => props.selectedId, rows], () => {
  if (!rows.value.some((r) => r.id === focusId.value))
    focusId.value = props.selectedId || rows.value[0]?.id || null
}, { immediate: true })

const rowEls = new Map()
const setRowEl = (id, el) => { el ? rowEls.set(id, el) : rowEls.delete(id) }
function focusAt(i) {
  const r = rows.value[Math.max(0, Math.min(rows.value.length - 1, i))]
  if (!r) return
  focusId.value = r.id
  nextTick(() => rowEls.get(r.id)?.focus())
}
function moveRight(idx, row) {
  if (row.hasChildren && !row.open) toggle(row.id)
  else if (row.hasChildren) focusAt(idx + 1)
}
function moveLeft(idx, row) {
  if (row.hasChildren && row.open && !q.value) { toggle(row.id); return }
  const p = parentOf.value[row.id]
  if (p) focusAt(rows.value.findIndex((r) => r.id === p))
}
const select = (i, row) => emit('select', row.id)
const KEYS = {
  ArrowDown: (i) => focusAt(i + 1),
  ArrowUp: (i) => focusAt(i - 1),
  Home: () => focusAt(0),
  End: () => focusAt(rows.value.length - 1),
  ArrowRight: moveRight,
  ArrowLeft: moveLeft,
  Enter: select,
  ' ': select,
}
function onKey(e, row) {
  const fn = KEYS[e.key]
  if (!fn) return
  fn(rows.value.findIndex((r) => r.id === row.id), row)
  e.preventDefault()
}
</script>

<template>
  <ul role="tree" aria-label="Topic taxonomy" class="space-y-0.5">
    <li
      v-for="row in rows"
      :key="row.id"
      role="treeitem"
      :aria-level="row.depth + 1"
      :aria-expanded="row.hasChildren ? row.open : undefined"
      :aria-selected="selectedId === row.id"
    >
      <div
        :ref="el => setRowEl(row.id, el)"
        :tabindex="focusId === row.id ? 0 : -1"
        :class="['group relative flex items-center gap-1.5 rounded-md pr-2 py-1.5 cursor-pointer select-none',
                 'focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]',
                 selectedId === row.id ? 'bg-primary/10 text-primary font-medium' : 'text-fg-muted hover:bg-surface-2']"
        @click="onRow(row)"
        @keydown="onKey($event, row)"
      >
        <!-- active accent rail -->
        <span v-if="selectedId === row.id" class="absolute left-0 top-1 bottom-1 w-0.5 rounded-full bg-primary" aria-hidden="true" />
        <!-- depth guide rails -->
        <span v-for="d in row.depth" :key="d" class="shrink-0 w-3.5 self-stretch border-l border-border-subtle" aria-hidden="true" />
        <!-- chevron -->
        <span class="shrink-0 w-3.5 grid place-items-center" aria-hidden="true">
          <Icon v-if="row.hasChildren" :name="row.open ? 'chevron-down' : 'chevron-right'" :size="13" class="text-fg-faint" />
        </span>
        <!-- label (with match highlight) -->
        <span class="flex-1 min-w-0 truncate text-sm" :title="row.node.label">
          <template v-for="(s, i) in segments(row.node.label)" :key="i"><mark v-if="s.hit" class="bg-warning-soft text-warning-strong rounded-sm px-0.5">{{ s.t }}</mark><template v-else>{{ s.t }}</template></template>
        </span>
        <!-- wiki indicator -->
        <span v-if="row.node.has_wiki" class="shrink-0 h-1.5 w-1.5 rounded-full bg-success" title="has a curated wiki page" />
        <!-- subtree memory count -->
        <span
          v-if="row.node.mem_count"
          class="shrink-0 text-[10px] font-mono tabular-nums px-1.5 py-0.5 rounded bg-surface-2 text-fg-subtle"
          title="memories in this subtree"
        >{{ row.node.mem_count }}</span>
      </div>
    </li>
  </ul>
</template>
