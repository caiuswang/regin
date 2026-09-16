import { computed, ref } from 'vue'

// Detail mode: the conversation feed's reading-density control, one enum with
// three exclusive values. Module-level state (same shape as
// useConversationRail) because the control lives in the trace header and the
// rows it governs live three components down — threading a prop through
// SessionTraceView → TraceConversationRegion → SessionConversationView would
// put the mode in four places that can disagree.
//
// The user-facing labels and the stored ids deliberately disagree: what reads
// as "Condensed" persists as 'focus' and what reads as "Focus" persists as
// 'latest'. Renaming one half would strand every already-saved preference.
const DETAIL_KEY = 'regin.detail'
const LEGACY_FOCUS_KEY = 'regin.focus'

export const DETAIL_FULL = 'full'
export const DETAIL_CONDENSED = 'focus'
export const DETAIL_LATEST = 'latest'

const VALID = new Set([DETAIL_FULL, DETAIL_CONDENSED, DETAIL_LATEST])

function readStored() {
  try {
    const v = window.localStorage.getItem(DETAIL_KEY)
    if (VALID.has(v)) return v
    // The boolean this control replaced. Read once so a reader who had Focus
    // on before the three-mode split lands in Condensed — the mode that does
    // what their toggle used to do — rather than silently back on Full.
    return window.localStorage.getItem(LEGACY_FOCUS_KEY) === '1' ? DETAIL_CONDENSED : DETAIL_FULL
  } catch (e) { return DETAIL_FULL }
}

const detail = ref(readStored())

// Tier 2 rows the reader opened by hand. Applied AFTER the tier, so flipping
// the mode never closes a body someone opened. Deliberately does not apply to
// Tier 3: promoting a folded span to a card would let one click quietly undo
// the mode.
const manuallyOpen = ref(new Set())
// `${turnId}:fold` (Condensed strip) / `${turnId}:latest` (Focus strip). The
// two kinds are keyed apart so switching mode leaves the other's opened strips
// exactly as they were — Focus → Full → Focus returns to the same page.
const openFolds = ref(new Set())

function setDetail(mode) {
  detail.value = VALID.has(mode) ? mode : DETAIL_FULL
  try { window.localStorage.setItem(DETAIL_KEY, detail.value) } catch (e) { /* private mode */ }
}

export function useFocusMode() {
  // Pressing a mode's own key while it is active returns to Full: the shortcut
  // is a toggle, so the reader never has to remember a third keystroke to get
  // the whole transcript back.
  function toggleDetail(mode) {
    setDetail(detail.value === mode ? DETAIL_FULL : mode)
  }
  function toggleCondensed() { toggleDetail(DETAIL_CONDENSED) }
  function toggleLatest() { toggleDetail(DETAIL_LATEST) }

  function isManuallyOpen(spanId) { return manuallyOpen.value.has(spanId) }
  function toggleManuallyOpen(spanId) {
    const next = new Set(manuallyOpen.value)
    if (next.has(spanId)) next.delete(spanId); else next.add(spanId)
    manuallyOpen.value = next
  }
  // A `?span=` deep link forces its target to full prominence by seeding the
  // manual set — from there it behaves exactly like a hand-opened row.
  function revealSpan(spanId) {
    if (!spanId || manuallyOpen.value.has(spanId)) return
    manuallyOpen.value = new Set(manuallyOpen.value).add(spanId)
  }

  const foldKey = (turnId, kind = 'fold') => `${turnId}:${kind}`
  function isFoldOpen(turnId, kind = 'fold') { return openFolds.value.has(foldKey(turnId, kind)) }
  function toggleFold(turnId, kind = 'fold') {
    const key = foldKey(turnId, kind)
    const next = new Set(openFolds.value)
    if (next.has(key)) next.delete(key); else next.add(key)
    openFolds.value = next
  }
  function openFold(turnId, kind = 'fold') {
    const key = foldKey(turnId, kind)
    if (!openFolds.value.has(key)) openFolds.value = new Set(openFolds.value).add(key)
  }

  // Per-session state living in module scope has to be retired explicitly, or
  // it follows the reader to the next trace: `manuallyOpen` and `openFolds`
  // hold span/turn ids that mean nothing there. Nothing else clears them — the
  // sets are unbounded for the tab's lifetime otherwise.
  function resetForTrace() {
    manuallyOpen.value = new Set()
    openFolds.value = new Set()
  }

  return {
    detail,
    setDetail,
    toggleCondensed,
    toggleLatest,
    condensedOn: computed(() => detail.value === DETAIL_CONDENSED),
    latestOn: computed(() => detail.value === DETAIL_LATEST),
    isManuallyOpen, toggleManuallyOpen, revealSpan,
    isFoldOpen, toggleFold, openFold,
    resetForTrace,
  }
}
