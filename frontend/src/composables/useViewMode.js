import { ref } from 'vue'

// The session trace's view modes, and the one list every consumer reads: the
// header's lg+ menu, the compact sticky strip below it, and the validator
// that resolves a persisted or deep-linked value. They were three separate
// literals that had to agree by hand.
export const MODE_OPTIONS = [
  { id: 'conversation', label: 'Conversation' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'log', label: 'Log' },
  { id: 'pane', label: 'Terminal' },
  { id: 'messages', label: 'Messages' },
]

// Resolution order: `?view=<mode>` query param > localStorage > default. The
// query param is honored WITHOUT writing to localStorage so deep links from
// /trace/triggers (which force conversation view so the surrounding prompt is
// visible) don't clobber the user's chosen default for casually-opened
// sessions. `setViewMode` is the only writer and it persists.
const VIEW_MODE_KEY = 'regin_session_view_mode'
const VALID_VIEW_MODES = MODE_OPTIONS.map(m => m.id)

// 'terminal' used to name the flat span log; it now names the real PTY pane,
// so every value already persisted or already linked to means the log. Read
// side only — nothing writes the old id back.
const RENAMED_VIEW_MODES = { terminal: 'log' }

function normalizeViewMode(mode) {
  const renamed = RENAMED_VIEW_MODES[mode] || mode
  return VALID_VIEW_MODES.includes(renamed) ? renamed : null
}

export function useViewMode(route) {
  function initialViewMode() {
    const fromQuery = route.query.view
    if (typeof fromQuery === 'string') {
      const normalized = normalizeViewMode(fromQuery)
      if (normalized) return normalized
    }
    return normalizeViewMode(localStorage.getItem(VIEW_MODE_KEY)) || 'conversation'
  }

  const viewMode = ref(initialViewMode())

  function setViewMode(mode) {
    viewMode.value = mode
    localStorage.setItem(VIEW_MODE_KEY, mode)
  }

  return { viewMode, setViewMode }
}
