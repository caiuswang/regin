import { ref } from 'vue'

// Desktop sidebar rail state: expanded (labels) vs collapsed (icons only).
// Module-level singleton so the rail and anything that mirrors it share one
// reactive value, mirroring useTheme.
const STORAGE_KEY = 'regin_sidebar_collapsed'

function initialCollapsed() {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

const collapsed = ref(initialCollapsed())

function setCollapsed(next) {
  collapsed.value = next
  try {
    localStorage.setItem(STORAGE_KEY, next ? '1' : '0')
  } catch { /* private mode: state stays in-memory for this session */ }
}

function toggleCollapsed() {
  setCollapsed(!collapsed.value)
}

// SELECT is in here because `\` is a type-ahead key for a native dropdown, not
// a free keystroke the app may claim.
const TYPING_TAGS = ['INPUT', 'TEXTAREA', 'SELECT']

function isTypingTarget(el) {
  if (!el) return false
  return TYPING_TAGS.includes(el.tagName) || el.isContentEditable
}

// The rail is `display:none` below 768px, so a shortcut there would silently
// persist a collapsed state the user never sees — and meet them collapsed the
// next time they widen the window.
function railIsVisible() {
  return window.matchMedia?.('(min-width: 768px)').matches ?? true
}

function onKeydown(e) {
  if (e.key !== '\\' || e.metaKey || e.ctrlKey || e.altKey) return
  if (isTypingTarget(e.target) || !railIsVisible()) return
  e.preventDefault()
  toggleCollapsed()
}

let keyboardBound = false

export function useSidebarCollapsed() {
  if (!keyboardBound && typeof window !== 'undefined') {
    window.addEventListener('keydown', onKeydown)
    keyboardBound = true
  }
  return { collapsed, setCollapsed, toggleCollapsed }
}
