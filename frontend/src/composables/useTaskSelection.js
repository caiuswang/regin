import { computed, ref, shallowRef } from 'vue'

/**
 * useTaskSelection — which rows the bulk bar is about to act on.
 *
 * Held as a Set of ids rather than as row objects: a selected task can be
 * re-fetched, re-ordered into another group, or edited between the click and
 * the bulk request, and an id survives all three where a stale object does
 * not.
 *
 * `visible` is what select-all is scoped to, and it is the union of what each
 * *source* — the flat feed, or one open group — currently renders. Keyed by
 * source rather than accumulated, because a group that collapses stops
 * rendering its rows: an accumulating set made "Select all N loaded" count and
 * then act on rows that are no longer on screen.
 */
export function useTaskSelection() {
  const ids = ref(new Set())
  const sources = shallowRef(new Map())

  const visible = computed(() => {
    const seen = new Set()
    for (const rows of sources.value.values()) {
      for (const id of rows) seen.add(id)
    }
    return [...seen]
  })

  const count = computed(() => ids.value.size)
  const allVisibleSelected = computed(() =>
    visible.value.length > 0 && visible.value.every(id => ids.value.has(id)))
  const someVisibleSelected = computed(() =>
    !allVisibleSelected.value && visible.value.some(id => ids.value.has(id)))

  function toggle(task) {
    const next = new Set(ids.value)
    if (next.has(task.id)) next.delete(task.id)
    else next.add(task.id)
    ids.value = next
  }

  /** What one source renders now — replacing, never adding to, what it did. */
  function reportVisible(source, rows) {
    const next = new Map(sources.value)
    next.set(source, rows.map(row => row.id))
    sources.value = next
  }

  /** A source that has stopped rendering: a collapsed group, or an unmount. */
  function withdrawVisible(source) {
    if (!sources.value.has(source)) return
    const next = new Map(sources.value)
    next.delete(source)
    sources.value = next
  }

  function resetVisible() {
    sources.value = new Map()
  }

  function toggleAllVisible() {
    if (allVisibleSelected.value) {
      const next = new Set(ids.value)
      for (const id of visible.value) next.delete(id)
      ids.value = next
      return
    }
    ids.value = new Set([...ids.value, ...visible.value])
  }

  function clear() {
    ids.value = new Set()
  }

  return {
    ids, count, visible, allVisibleSelected, someVisibleSelected,
    toggle, toggleAllVisible, reportVisible, withdrawVisible, resetVisible,
    clear,
  }
}
