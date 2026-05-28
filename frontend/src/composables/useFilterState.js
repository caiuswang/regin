import { ref, watch } from 'vue'

/**
 * useFilterState — localStorage-backed reactive filter state.
 *
 * Replaces the ad-hoc `ref(localStorage.getItem(KEY) || default)` +
 * manual setter pattern that SessionsView and PatternsView both reach
 * for. Updates persist automatically; an optional `validate` callback
 * (typically `value => OPTIONS.some(o => o.value === value)`) discards
 * a stored value when the option list changes between releases.
 *
 * Example:
 *   const range = useFilterState('regin.sessions.range', 'today',
 *     v => RANGE_OPTIONS.some(o => o.value === v))
 *   // ... range.value = 'this-week'  → persists automatically
 *
 * @param {string} key — localStorage key (namespace with a `regin.` prefix).
 * @param {any} defaultValue — value to use when nothing stored or
 *   `validate` rejects the stored value.
 * @param {(value: any) => boolean} [validate] — optional guard.
 *
 * Returns a `Ref` whose value is two-way synced with localStorage.
 */
export function useFilterState(key, defaultValue, validate = null) {
  const stored = localStorage.getItem(key)
  // localStorage stores strings only; non-string defaults stay raw and
  // are revived through JSON when set/get goes through this composable.
  const initial = _resolveInitial(stored, defaultValue, validate)
  const state = ref(initial)

  watch(state, (v) => {
    try {
      if (v == null) {
        localStorage.removeItem(key)
        return
      }
      if (typeof v === 'string') {
        localStorage.setItem(key, v)
      } else {
        localStorage.setItem(key, JSON.stringify(v))
      }
    } catch {
      // Quota exceeded / private browsing — fall through silently.
      // The filter still works in-memory for this session.
    }
  })

  return state
}

function _resolveInitial(stored, defaultValue, validate) {
  if (stored == null) return defaultValue
  let candidate = stored
  if (typeof defaultValue !== 'string' && defaultValue != null) {
    try { candidate = JSON.parse(stored) } catch { return defaultValue }
  }
  if (validate && !validate(candidate)) return defaultValue
  return candidate
}
