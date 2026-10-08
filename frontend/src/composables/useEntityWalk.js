// The palette's preview focus (R5/R8, axis 2): which entity the right pane is
// showing, independent of which row the list cursor is on. The list row is the
// origin; ⌥→ pushes the picked relation onto a stack, ⌥← / esc pops one step,
// and moving the list cursor starts over from the new row.
//
// Every request here is a GET. The palette navigates and copies; it never
// changes state (Non-goals), so nothing in this module may post.
import { computed, reactive, ref, watch } from 'vue'
import api from '../api'
import { paletteRowOf } from '../utils/sessionSwitcher.js'

export const ENTITY_KINDS = new Set(['feature', 'task', 'defect', 'session'])

const REF_ID = /^[FTD](\d+)$/

const ROUTE = {
  feature: id => `/features/${id}`,
  task: id => `/tasks/${id}`,
  defect: id => `/defects/${id}`,
  session: id => `/trace/sessions/${id}`,
}

// A session is known by its full trace id where the payload carries one (a
// search document, a relation target), and only by the `#` ref's eight
// characters where it does not (a relation list's rows). The prefix is
// resolved through the same lookup `#` addressing uses.
function sessionNode(ref, id) {
  const full = id && !String(id).startsWith('#') ? String(id) : null
  return { kind: 'session', id: full, prefix: full || String(ref || id || '').replace(/^#/, '') }
}

export function nodeOf({ kind, id, ref, title }) {
  if (!ENTITY_KINDS.has(kind)) return null
  const base = kind === 'session'
    ? sessionNode(ref, id)
    : { kind, id: id ?? REF_ID.exec(ref || '')?.[1] ?? null }
  return { ...base, ref: ref || '', title: title || '', status: 'loading', data: null, session: null }
}

// A roster row is already the switcher's answer for that session — its tail
// included — so the walk starts from it instead of looking the id up again.
export function sessionOrigin(row) {
  if (!row?.trace_id) return null
  const { ref, title } = paletteRowOf(row)
  const node = nodeOf({ kind: 'session', id: row.trace_id, ref, title })
  return { ...node, session: row }
}

export function originOf(entry) {
  const doc = entry?.doc
  if (!doc?.row || !ENTITY_KINDS.has(doc.kind)) return null
  return nodeOf({ kind: doc.kind, id: doc.id, ref: doc.row.ref, title: doc.row.title })
}

async function lookupSession(prefix) {
  const data = await api.get(`/session-switcher?id=${encodeURIComponent(prefix)}&tail=1`)
  const rows = data.sessions || []
  return rows.find(r => r.trace_id === prefix)
    || rows.find(r => r.trace_id.startsWith(prefix)) || rows[0] || null
}

async function hydrate(node) {
  if (node.kind === 'session' && !node.session) {
    node.session = await lookupSession(node.prefix)
    if (!node.id) node.id = node.session?.trace_id || null
    if (!node.id) throw new Error(`no session ${node.ref}`)
  }
  node.data = await api.get(`/palette/relations/${node.kind}/${encodeURIComponent(node.id)}`)
  if (node.data?.row?.ref) node.ref = node.data.row.ref
  node.status = 'ok'
}

// What ⌥↑↓ can land on, in reading order: each relation that names
// something, then each list's rows, then its "All N →" when it holds rows back.
export function pickablesOf(data) {
  if (!data) return []
  const out = []
  ;(data.relations || []).forEach((rel, i) => {
    const node = rel.target?.none ? null : nodeOf(rel.target || {})
    if (node) out.push({ key: `rel:${i}`, type: 'node', node })
  })
  ;(data.lists || []).forEach((list, li) => {
    list.items.forEach((row, ri) => {
      const node = nodeOf(row)
      if (node) out.push({ key: `list:${li}:${ri}`, type: 'node', node })
    })
    if (list.total > list.items.length) out.push({ key: `all:${li}`, type: 'all', list })
  })
  return out
}

// "All N →" leaves the palette for the page that lists every one. The search
// API has no feature filter, so a Drill cannot be scoped to one; the list
// pages that can be are named, and the rest open the entity that owns them.
function allHref(node, list) {
  if (node.kind === 'feature' && list.label === 'Tasks') return `/tasks?feature_id=${node.id}`
  if (node.kind === 'feature' && list.label === 'Open defects') return `/defects?feature_id=${node.id}`
  return ROUTE[node.kind](node.id)
}

function copyText(node) {
  return node.kind === 'session' ? node.id : node.ref
}

const PRIMARY_LABEL = { session: 'Attach', task: 'Open task', feature: 'Open ledger', defect: 'Open defect' }

function runsTarget(data) {
  const runs = (data?.lists || []).find(l => l.label === 'Runs')
  return runs?.items[0] ? nodeOf(runs.items[0]) : null
}

function reporterTarget(data) {
  const rel = (data?.relations || []).find(r => r.rel === 'Reported in')
  return rel && !rel.target?.none ? nodeOf(rel.target) : null
}

// R6, one row per kind: ↵, ⌘↵, then Copy id. Nothing here writes.
function secondaryOf(node) {
  if (node.kind === 'session') return { label: 'New tab', type: 'tab', href: ROUTE.session(node.id) }
  if (node.kind === 'feature') return { label: 'Its tasks', type: 'route', href: `/tasks?feature_id=${node.id}` }
  const target = node.kind === 'task' ? runsTarget(node.data) : reporterTarget(node.data)
  return { label: node.kind === 'task' ? 'Latest run' : 'Reporter', type: 'pivot', target }
}

export function actionsOf(node) {
  if (!node?.id) return null
  const secondary = secondaryOf(node)
  return {
    primary: { label: PRIMARY_LABEL[node.kind], type: 'route', href: ROUTE[node.kind](node.id) },
    secondary: { ...secondary, disabled: secondary.type === 'pivot' && !secondary.target },
    copy: { label: 'Copy id', type: 'copy', text: copyText(node) },
  }
}

export function useEntityWalk() {
  const stack = ref([])
  const pickKey = ref(null)
  // Bumped by every reset, so an origin's answer that lands after the cursor
  // moved on is dropped instead of painting the wrong entity.
  let generation = 0

  const focus = computed(() => stack.value[stack.value.length - 1] || null)
  const origin = computed(() => stack.value[0] || null)
  const pivoted = computed(() => stack.value.length > 1)
  const pickables = computed(() => pickablesOf(focus.value?.data))
  const picked = computed(() =>
    pickables.value.find(p => p.key === pickKey.value) || null)
  const actions = computed(() => (focus.value?.status === 'ok' ? actionsOf(focus.value) : null))

  async function load(node, gen) {
    node.status = 'loading'
    try {
      await hydrate(node)
    } catch {
      node.status = 'failed'
    }
    if (gen === generation && node === focus.value) {
      pickKey.value = pickables.value[0]?.key ?? null
    }
  }

  function push(node) {
    if (!node) return
    const live = reactive(node)
    pickKey.value = null
    stack.value = [...stack.value, live]
    load(live, generation)
  }

  function reset(entry) {
    start(originOf(entry))
  }

  function start(node) {
    generation += 1
    stack.value = []
    pickKey.value = null
    if (node) push(node)
  }

  function retry() {
    if (focus.value) load(focus.value, generation)
  }

  function movePick(delta) {
    const list = pickables.value
    if (!list.length) return
    const at = list.findIndex(p => p.key === pickKey.value)
    const next = at < 0 ? 0 : (at + delta + list.length) % list.length
    pickKey.value = list[next].key
  }

  // Follows the picked node, or hands back the route an "All N →" opens.
  function follow(key = pickKey.value) {
    const target = pickables.value.find(p => p.key === key)
    if (!target) return null
    if (target.type === 'all') {
      return { type: 'route', href: allHref(focus.value, target.list) }
    }
    push({ ...target.node })
    return null
  }

  // One step back. Returns whether there was a step to take, so esc can fall
  // through to the next layer when the preview is already at its origin.
  function back() {
    if (!pivoted.value) return false
    popTo(stack.value.length - 2)
    return true
  }

  function popTo(depth) {
    if (depth < 0 || depth >= stack.value.length - 1) return
    stack.value = stack.value.slice(0, depth + 1)
    pickKey.value = pickables.value[0]?.key ?? null
  }

  return {
    stack, focus, origin, pivoted, pickables, picked, pickKey, actions,
    reset, start, retry, movePick, follow, back, popTo, push,
  }
}

// Which row the walk starts from: the roster's highlighted session in ⌘J, the
// search cursor otherwise. Keyed on the row, not the row object: a streaming
// group (or the roster's poll) rebuilds its rows without the cursor moving,
// and that must not unwind a walk. Closing the palette drops the walk.
export function trackWalkOrigin(walk, { open, roster, rosterRow, activeRow }) {
  const key = computed(() => {
    if (!open.value) return null
    if (roster.value) return rosterRow.value ? `roster:${rosterRow.value.trace_id}` : null
    return activeRow.value?.key ?? null
  })
  watch(key, () => {
    if (!key.value) walk.start(null)
    else if (roster.value) walk.start(sessionOrigin(rosterRow.value))
    else walk.reset(activeRow.value)
  }, { immediate: true })
  // The origin was seeded from the roster row; each poll brings a fresher tail
  // for the same session, and the preview should read it.
  watch(rosterRow, (row) => {
    const origin = walk.origin.value
    if (roster.value && row && origin?.id === row.trace_id) origin.session = row
  })
}
