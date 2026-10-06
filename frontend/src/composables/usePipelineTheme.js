import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { DEFAULT_THEME, THEMES, THEME_LIST, classicTheme, isThemeId } from '../pipeline/themes/registry'
import { useFlash } from './useFlash'

// The pipeline shell's theme: 'classic' | 'pixel' (registry.js). A viewer
// preference like light/dark, and orthogonal to it — `data-theme` stays owned
// by useTheme, this one owns `data-pl-theme`.
const THEME_KEY = 'regin_pipeline_theme'
const ATTRIBUTE = 'data-pl-theme'

function storedTheme() {
  const stored = localStorage.getItem(THEME_KEY)
  return isThemeId(stored) ? stored : DEFAULT_THEME
}

const preference = ref(storedTheme())
const loaded = new Map([[DEFAULT_THEME, classicTheme]])
// The theme actually rendering: the preference once its module is in, or
// classic when that module failed. A fallback never rewrites the preference.
const activeId = ref(null)
const status = ref('resolving')
// What `<html>` carries — null whenever no shell tab is mounted.
const applied = ref(null)
let request = 0

async function resolve(id) {
  const token = ++request
  if (loaded.has(id)) {
    activeId.value = id
    status.value = 'ready'
    return
  }
  status.value = 'resolving'
  try {
    const module = await THEMES[id].load()
    loaded.set(id, module)
    if (token !== request) return
    activeId.value = id
  } catch {
    if (token !== request) return
    activeId.value = DEFAULT_THEME
    useFlash().flash(`${THEMES[id].label} theme failed to load — showing ${THEMES[DEFAULT_THEME].label}`, 'error')
  }
  status.value = 'ready'
}

function apply(id) {
  applied.value = id
  if (id) document.documentElement.setAttribute(ATTRIBUTE, id)
  else document.documentElement.removeAttribute(ATTRIBUTE)
}

export function usePipelineTheme() {
  function setPreference(next) {
    if (!isThemeId(next) || (next === preference.value && next === activeId.value)) return
    preference.value = next
    localStorage.setItem(THEME_KEY, next)
    resolve(next)
  }

  return {
    themes: THEME_LIST,
    preference,
    activeId,
    ready: computed(() => status.value === 'ready'),
    setPreference,
  }
}

// The shell's half: resolve the stored preference on mount, and mirror the
// active theme onto <html> only while `onTab()` holds. The previous theme
// stays applied while the next one loads, so the header never goes bare. On <html>, not the
// shell, because the create dialogs, the picker and toasts teleport to <body>.
export function useShellTheme(onTab) {
  if (activeId.value !== preference.value) resolve(preference.value)
  watch(
    () => (onTab() ? activeId.value : null),
    apply,
    { immediate: true },
  )
  onBeforeUnmount(() => apply(null))
}

// The module slots and glyphs resolve against: classic wherever the shell
// has not applied a theme — detail pages, and everything outside the shell.
export function useAppliedTheme() {
  return computed(() => (applied.value && loaded.get(applied.value)) || classicTheme)
}
