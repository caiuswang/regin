import { ref } from 'vue'

/**
 * useExpandState — reactive Set of expanded ids with toggle/has helpers.
 *
 * SessionConversationView tracks ~10 of these (prompts, prompt-bodies,
 * Bash output, diffs, tool-search attrs, ...) all written as
 * `ref(new Set())` plus inline `set.has(id) / set.add(id) / set.delete(id)`
 * dances. This composable folds that pattern into a single object so
 * each view section only needs one declaration line.
 *
 * Triggers reactivity correctly: Vue doesn't track Set mutations, so we
 * reassign a fresh Set on each mutation rather than mutating in place.
 *
 * @param {Iterable<string|number>} [initial] — ids expanded on mount.
 *
 * Returns:
 *   - `expanded` — `Ref<Set<id>>`. Read-only by convention; mutate via
 *     `toggle` / `add` / `remove` / `clear` / `setAll`.
 *   - `isExpanded(id)` — boolean.
 *   - `toggle(id)` — flip one id's state.
 *   - `add(id)` / `remove(id)` — explicit on/off.
 *   - `clear()` — empty the set.
 *   - `setAll(ids)` — replace the set with the given iterable.
 */
export function useExpandState(initial = []) {
  const expanded = ref(new Set(initial))

  function isExpanded(id) {
    return expanded.value.has(id)
  }

  function _replace(next) {
    expanded.value = next
  }

  function toggle(id) {
    const next = new Set(expanded.value)
    if (next.has(id)) {
      next.delete(id)
    } else {
      next.add(id)
    }
    _replace(next)
  }

  function add(id) {
    if (expanded.value.has(id)) return
    const next = new Set(expanded.value)
    next.add(id)
    _replace(next)
  }

  function remove(id) {
    if (!expanded.value.has(id)) return
    const next = new Set(expanded.value)
    next.delete(id)
    _replace(next)
  }

  function clear() {
    if (expanded.value.size === 0) return
    _replace(new Set())
  }

  function setAll(ids) {
    _replace(new Set(ids))
  }

  return {
    expanded, isExpanded,
    toggle, add, remove, clear, setAll,
  }
}
