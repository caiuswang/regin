import { ref } from 'vue'

// View-mode selection for the session trace: 'conversation' | 'timeline' |
// 'terminal' | 'messages'. Resolution order: `?view=<mode>` query param > localStorage >
// default. The query param is honored WITHOUT writing to localStorage so deep
// links from /trace/triggers (which force conversation view so the surrounding
// prompt is visible) don't clobber the user's chosen default for casually-
// opened sessions. `setViewMode` is the only writer and it persists.
const VIEW_MODE_KEY = 'regin_session_view_mode'
const VALID_VIEW_MODES = ['conversation', 'timeline', 'terminal', 'messages']

export function useViewMode(route) {
  function initialViewMode() {
    const fromQuery = route.query.view
    if (typeof fromQuery === 'string' && VALID_VIEW_MODES.includes(fromQuery)) {
      return fromQuery
    }
    return localStorage.getItem(VIEW_MODE_KEY) || 'conversation'
  }

  const viewMode = ref(initialViewMode())

  function setViewMode(mode) {
    viewMode.value = mode
    localStorage.setItem(VIEW_MODE_KEY, mode)
  }

  return { viewMode, setViewMode }
}
