import { computed, ref } from 'vue'
import api from '../api'
import { isRemoved, mergeDeps, pendingAdds, pick, toggleRemove } from '../utils/graphEdits.js'

/**
 * Dependency editing on the lane graph: a source pick, a kind, and the pending
 * edits batched per source task until Save.
 *
 * Each save re-reads the source's own edges first rather than trusting what is
 * drawn — the lane graph hides finished lanes, and a PUT built from the drawing
 * would silently drop every edge it was not showing.
 */
export function useGraphEdit({ edgeExists, afterSave }) {
  const editing = ref(false)
  const source = ref(null)
  const kind = ref('requires')
  const pending = ref(new Map())
  const saving = ref(false)
  const refusal = ref(null)

  const changedCount = computed(() => pending.value.size)
  const adds = computed(() => pendingAdds(pending.value))

  function clearSource() {
    source.value = null
    kind.value = 'requires'
  }

  function pickNode(id) {
    if (saving.value) return
    if (source.value == null) {
      source.value = id
      return
    }
    if (source.value !== id) {
      pending.value = pick(pending.value, {
        sourceId: source.value,
        targetId: id,
        kind: kind.value,
        existing: edgeExists(source.value, id),
      })
    }
    clearSource()
  }

  function toggleEdge(sourceId, dependsOnId) {
    if (saving.value) return
    pending.value = toggleRemove(pending.value, sourceId, dependsOnId)
  }

  function removed(sourceId, dependsOnId) {
    return isRemoved(pending.value, sourceId, dependsOnId)
  }

  async function serverEdges(sourceId) {
    const data = await api.get(`/tasks/graph?root=${sourceId}&depth=1`)
    return (data.edges || [])
      .filter(e => e.task_id === sourceId)
      .map(e => ({ id: e.depends_on_id, kind: e.kind || 'requires' }))
  }

  async function saveOne(sourceId, change) {
    const result = await api.put(`/tasks/${sourceId}/deps`,
      { depends_on: mergeDeps(await serverEdges(sourceId), change) })
    if (result.ok !== false) return null
    return { sourceId, message: result.msg || 'The server refused these dependencies.',
      pair: result.body?.pair || [] }
  }

  async function save() {
    saving.value = true
    refusal.value = null
    try {
      for (const [sourceId, change] of [...pending.value]) {
        const refused = await saveOne(sourceId, change)
        if (refused) {
          refusal.value ??= refused
          continue
        }
        const next = new Map(pending.value)
        next.delete(sourceId)
        pending.value = next
      }
    } catch (e) {
      refusal.value = { sourceId: null, message: e?.message || String(e), pair: [] }
    } finally {
      saving.value = false
    }
    await afterSave()
  }

  function setEditing(on) {
    if (saving.value) return
    editing.value = on
    clearSource()
    if (!on) {
      pending.value = new Map()
      refusal.value = null
    }
  }

  return {
    editing, source, kind, pending, saving, refusal, changedCount, adds,
    pickNode, toggleEdge, removed, clearSource, save, setEditing,
    discard: () => setEditing(false),
  }
}
