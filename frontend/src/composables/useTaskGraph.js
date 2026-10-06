import { computed, ref } from 'vue'
import api from '../api'
import { layoutGraph } from '../utils/taskGraphLayout.js'
import { taskTitle } from '../utils/taskTitle.js'

export const DEPTH_MIN = 1
export const DEPTH_MAX = 6
export const DEPTH_DEFAULT = 2

// Past this many nodes a layered drawing is unreadable at any zoom. It is sent
// per request rather than assumed by the server because the dependency editor
// reads the same endpoint for its candidate list, where a cap would silently
// offer nothing.
export const DRAW_NODE_CAP = 150

function graphPath(root, depth, status, cap) {
  const params = new URLSearchParams()
  if (root != null) {
    params.set('root', String(root))
    params.set('depth', String(depth))
  }
  if (status) params.set('status', status)
  if (cap != null) params.set('cap', String(cap))
  const qs = params.toString()
  return qs ? `/tasks/graph?${qs}` : '/tasks/graph'
}

function clampDepth(value) {
  const depth = Number(value)
  if (!Number.isFinite(depth)) return DEPTH_DEFAULT
  return Math.min(DEPTH_MAX, Math.max(DEPTH_MIN, Math.round(depth)))
}

/**
 * useTaskGraph — `/api/tasks/graph`, and the layered layout drawn from it.
 *
 * One fetch answers both consumers: the DAG panel draws it, and the dependency
 * editor reads the same nodes as its candidate list and the same edges as the
 * task's current blockers. There is no second endpoint for "what does X depend
 * on", and deriving it here keeps the two views from disagreeing.
 *
 * Unfocused, the server may answer `truncated` — the real size of a graph it
 * refuses to serialize whole. That is a state to render, not an error: nodes
 * are legitimately empty and the reader is being asked for a root.
 */
export function useTaskGraph({ cap = null } = {}) {
  const nodes = ref([])
  const edges = ref([])
  const loading = ref(true)
  const error = ref('')
  const root = ref(null)
  const depth = ref(DEPTH_DEFAULT)
  const status = ref('')
  const truncated = ref(false)
  const totalNodes = ref(0)
  const nodeCap = ref(0)

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const data = await api.get(
        graphPath(root.value, depth.value, status.value, cap))
      nodes.value = data.nodes || []
      edges.value = data.edges || []
      truncated.value = !!data.truncated
      totalNodes.value = data.total_nodes ?? nodes.value.length
      nodeCap.value = data.node_cap ?? 0
    } catch (e) {
      error.value = e?.message || 'Failed to read the task graph.'
    } finally {
      loading.value = false
    }
  }

  function focus(taskId) {
    const id = taskId == null ? null : Number(taskId)
    root.value = Number.isFinite(id) ? id : null
    return load()
  }

  // Depth only scopes a walk that has a root, so without one there is nothing
  // to re-read: the value is kept for the next focus instead.
  function setDepth(value) {
    depth.value = clampDepth(value)
    return root.value == null ? Promise.resolve() : load()
  }

  function setStatus(value) {
    status.value = value || ''
    return load()
  }

  const layout = computed(() => layoutGraph(nodes.value, edges.value))

  // Edges, not ids: the editor has to show which of the two things each one
  // means, and an id list cannot carry that.
  function depEdgesOf(taskId) {
    return edges.value
      .filter(e => e.task_id === taskId)
      .map(e => ({ id: e.depends_on_id, kind: e.kind || 'requires' }))
  }

  function titleOf(taskId) {
    const node = nodes.value.find(n => n.id === taskId)
    return (node && taskTitle(node)) || `task ${taskId}`
  }

  return {
    nodes, edges, loading, error, layout, load, depEdgesOf, titleOf,
    root, depth, status, truncated, totalNodes, nodeCap,
    focus, setDepth, setStatus,
  }
}
