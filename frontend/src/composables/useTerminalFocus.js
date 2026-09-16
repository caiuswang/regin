import { ref } from 'vue'

// Whether a live pane terminal currently has the keyboard.
//
// The SPA owns several global chords bound in the capture phase at `window`
// (⌘K / ⌘J in useSearchShortcuts, `\` in useSidebarCollapsed, `t` in
// useConversationRail). Capture runs before anything inside the terminal, so
// those keys never reach the pane — which is wrong for a surface whose whole
// contract is that every key goes through untranslated. The two rail chords
// already bail on a typing target and xterm's helper element is a <textarea>,
// so they are fine; the ⌘ chords fire unconditionally and are not.
//
// A shared flag rather than a DOM check: the chord handlers run before the
// event reaches the terminal, so "did this land in the terminal" is not yet
// answerable from the event, but "is the terminal holding focus" is.
const focused = ref(false)

export function useTerminalFocus() {
  return {
    focused,
    claim: () => { focused.value = true },
    release: () => { focused.value = false },
  }
}
