import { ref, computed, onMounted, onUnmounted } from 'vue'

// Resizable left-rail width: dragged via the handle on the rail's right edge
// (or ←/→ when focused) and persisted in localStorage. Clamped so the rail
// can't collapse or swallow the chat column. Default 224px = the previous
// fixed `w-56`.
const RAIL_MIN = 176
const RAIL_MAX = 560
const RAIL_KEY = 'regin_trace_rail_width'

// Mini form: numbered chips only, no prompt previews.
const RAIL_MINI_WIDTH = 62
const COLLAPSED_KEY = 'regin_trace_rail_collapsed'

// Below 1120px the viewport forces the mini form. Module-level rather than a
// useBreakpoint flag because the `t` handler is module-level too and has to
// read the same value — an instance-scoped ref left the key able to rewrite a
// preference the chevron refuses to touch.
const wideMq = typeof window !== 'undefined' && window.matchMedia
  ? window.matchMedia('(min-width: 1120px)')
  : null
const isWide = ref(wideMq ? wideMq.matches : true)
wideMq?.addEventListener('change', () => { isWide.value = wideMq.matches })

// The width alone decides the form here, so the toggle is inert in BOTH
// directions: expanding a width-folded rail is as invisible as collapsing it,
// and either click would still write COLLAPSED_KEY.
const autoFolded = computed(() => !isWide.value)
const railMini = computed(() => railCollapsed.value || autoFolded.value)

function clampRail(w) { return Math.min(RAIL_MAX, Math.max(RAIL_MIN, w)) }

function initialCollapsed() {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

// Module-level singleton (mirrors useSidebarCollapsed) so the module-level
// keyboard binding and every rail instance share one reactive value.
const railCollapsed = ref(initialCollapsed())

function setRailCollapsed(next) {
  railCollapsed.value = next
  try {
    localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0')
  } catch { /* private mode: state stays in-memory for this session */ }
}

function toggleRailCollapsed() {
  setRailCollapsed(!railCollapsed.value)
}

// SELECT is in here because `t` is a type-ahead key for a native dropdown, not
// a free keystroke the app may claim.
const TYPING_TAGS = ['INPUT', 'TEXTAREA', 'SELECT']

function isTypingTarget(el) {
  if (!el) return false
  return TYPING_TAGS.includes(el.tagName) || el.isContentEditable
}

// Mounted rails. The listener is bound for the SPA's lifetime (as `\\` is in
// useSidebarCollapsed), so without this `t` would toggle rail state from every
// other route — a write with nothing on screen to show for it.
let mountedRails = 0

function onKeydown(e) {
  if (e.key !== 't' || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return
  if (isTypingTarget(e.target) || mountedRails <= 0 || autoFolded.value) return
  e.preventDefault()
  toggleRailCollapsed()
}

let keyboardBound = false

export function useConversationRail() {
  if (!keyboardBound && typeof window !== 'undefined') {
    window.addEventListener('keydown', onKeydown)
    keyboardBound = true
  }
  onMounted(() => { mountedRails += 1 })
  onUnmounted(() => { mountedRails -= 1 })

  const railWidth = ref((() => {
    const v = parseInt(localStorage.getItem(RAIL_KEY), 10)
    return Number.isFinite(v) ? clampRail(v) : 224
  })())

  const effectiveWidth = computed(() => (railMini.value ? RAIL_MINI_WIDTH : railWidth.value))

  let startX = 0
  let startW = 0

  function onMove(e) {
    railWidth.value = clampRail(startW + (e.clientX - startX))
  }
  function onEnd() {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onEnd)
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    localStorage.setItem(RAIL_KEY, String(Math.round(railWidth.value)))
  }
  function onRailResizeStart(e) {
    startX = e.clientX
    startW = railWidth.value
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onEnd)
    document.body.style.userSelect = 'none'      // suppress text selection while dragging
    document.body.style.cursor = 'col-resize'
    e.preventDefault()
  }
  function onRailResizeKey(e) {
    const step = e.shiftKey ? 32 : 8
    if (e.key === 'ArrowLeft') railWidth.value = clampRail(railWidth.value - step)
    else if (e.key === 'ArrowRight') railWidth.value = clampRail(railWidth.value + step)
    else return
    e.preventDefault()
    localStorage.setItem(RAIL_KEY, String(Math.round(railWidth.value)))
  }

  return {
    railWidth, railCollapsed, railMini, autoFolded, effectiveWidth,
    toggleRailCollapsed, onRailResizeStart, onRailResizeKey,
  }
}
