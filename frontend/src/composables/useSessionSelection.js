import { computed, ref } from 'vue'

// Row selection for the sessions list, keyed by trace_id. `orderedIds` is the
// order the rows are on screen (across groups), which is what a shift-click
// range spans — the loaded page order would cut across group boundaries.
export function useSessionSelection(rows, orderedIds) {
  const selectedIds = ref(new Set())
  let anchor = null

  const selectionCount = computed(() => selectedIds.value.size)
  const allSelected = computed(() =>
    rows.value.length > 0 && rows.value.every(s => selectedIds.value.has(s.trace_id)))
  const selectedRows = computed(() => rows.value.filter(s => selectedIds.value.has(s.trace_id)))

  function rangeTo(traceId) {
    const ids = orderedIds.value
    const from = anchor ? ids.indexOf(anchor) : -1
    const to = ids.indexOf(traceId)
    if (from < 0 || to < 0) return [traceId]
    return ids.slice(Math.min(from, to), Math.max(from, to) + 1)
  }

  // A shift-click sets every row between the last-clicked one and this one
  // to THIS row's new state, so the same gesture extends or trims a range.
  function toggleOne(traceId, checked, shift = false) {
    const next = new Set(selectedIds.value)
    for (const id of shift ? rangeTo(traceId) : [traceId]) {
      if (checked) next.add(id)
      else next.delete(id)
    }
    selectedIds.value = next
    anchor = traceId
  }

  function toggleSelectAll(checked) {
    selectedIds.value = checked ? new Set(rows.value.map(s => s.trace_id)) : new Set()
    anchor = null
  }

  function clearSelection() {
    selectedIds.value = new Set()
    anchor = null
  }

  // Selection was computed against the previous page set; drop entries that
  // are no longer visible so a later batch action can't target rows the user
  // can't currently see.
  function pruneSelection() {
    const visible = new Set(rows.value.map(s => s.trace_id))
    selectedIds.value = new Set([...selectedIds.value].filter(id => visible.has(id)))
    if (anchor && !visible.has(anchor)) anchor = null
  }

  return {
    selectedIds, selectionCount, allSelected, selectedRows,
    toggleOne, toggleSelectAll, clearSelection, pruneSelection,
  }
}
