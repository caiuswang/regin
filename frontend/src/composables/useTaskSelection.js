import { computed, ref, shallowRef } from 'vue'

/**
 * useTaskSelection — which rows the bulk bar is about to act on.
 *
 * Held as a Set of ids rather than as row objects: a selected task can be
 * re-fetched, re-ordered into another group, or edited between the click and
 * the bulk request, and an id survives all three where a stale object does
 * not.
 *
 * `visible` is the set of ids currently rendered, reported by the groups as
 * they load. It is what select-all is scoped to — selecting rows that are not
 * on screen is an action nobody asked for, and the tri-state header checkbox
 * needs a denominator.
 */
export function useTaskSelection() {
  const ids = ref(new Set())
  const visible = shallowRef([])

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

  function reportVisible(rows) {
    const seen = new Set(visible.value)
    for (const row of rows) seen.add(row.id)
    visible.value = [...seen]
  }

  function resetVisible() {
    visible.value = []
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
    toggle, toggleAllVisible, reportVisible, resetVisible, clear,
  }
}
