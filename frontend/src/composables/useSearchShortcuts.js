import { onMounted, onBeforeUnmount } from 'vue'
import { useFullTextSearch } from './useFullTextSearch.js'
import { useSessionSwitcher } from './useSessionSwitcher.js'
import { useTerminalFocus } from './useTerminalFocus.js'

// The global entry point into the palette, bound once by the app shell.
//
// Two chords open it, and they differ only in what they pre-fill. ⌘K is the
// palette as you left it. ⌘J is the same palette with the Sessions facet
// forced and the query empty — the switcher — which is exactly the
// relationship ⌘F once had to Spans. The difference is that ⌘J's forcing is a
// non-persisting override (`scopeOverride`) dropped on close: ⌘F pinned the
// stored facet instead, so the pin outlived the open it was meant for and a
// later ⌘K silently stayed narrowed. Scope itself is a filter inside the
// palette, so ⌘F has nothing left to say and is gone.
//
// ⌃1–⌃9 attach by number with the palette CLOSED — the rail's keyboard half,
// modelled on tmux' `prefix 0–9`. It is deliberately a named chord rather than
// "any modifier + digit": the browser owns ⌘1–⌘9 (tab switching) and a modal
// that swallows by modifier class rather than by named chord is how a palette
// becomes un-typeable inside a dialog.
//
// The handler runs on `keydown` at the document and stops propagation for the
// combination it owns, so it lands BEFORE presentation mode's and the feed's
// handlers. ⌘K deliberately works from anywhere — including inside the
// composer and inside presentation mode, over which the palette stacks
// (--z-search) and to which esc returns.

const TYPING_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT'])

function isTyping(target) {
  if (!target) return false
  return TYPING_TAGS.has(target.tagName) || target.isContentEditable
}

// Feed views where a bare `/` is a search affordance rather than a character.
const SLASH_ROUTES = ['/trace/sessions', '/live', '/inbox']

export function useSearchShortcuts() {
  const search = useFullTextSearch()
  const switcher = useSessionSwitcher()
  const terminalFocus = useTerminalFocus()

  function onKeydown(e) {
    // A focused pane terminal owns the keyboard outright. These chords are
    // bound in the capture phase, so without this they are swallowed before
    // the terminal ever sees them — and a terminal that eats ⌘K is not a
    // terminal. Click out of it (or leave the tab) to get the chords back.
    if (terminalFocus.focused.value) return
    const mod = e.metaKey || e.ctrlKey
    // A field that binds ⌘K itself (the rich editor's link) marks itself
    // `data-owns-mod-k`; there the chord is the field's, as it is in the editors
    // the writer knows it from.
    if (mod && (e.key === 'k' || e.key === 'K') && !e.target?.closest?.('[data-owns-mod-k]')) {
      e.preventDefault()
      e.stopPropagation()
      search.openPalette()
      return
    }
    if (mod && (e.key === 'j' || e.key === 'J')) {
      e.preventDefault()
      e.stopPropagation()
      search.openSwitcher()
      return
    }
    // Palette closed only: with it open, the digits are the in-palette
    // hotkeys and the input may be taking a literal character. The same two
    // guards `/` carries apply — a chord that navigates away must never fire
    // out from under a caret (it would drop an unsent composer draft) or from
    // under a dialog the reader is mid-way through.
    if (e.ctrlKey && !e.metaKey && !e.altKey && /^[1-9]$/.test(e.key)
        && !search.open.value && !isTyping(e.target)
        && !document.querySelector('[role="dialog"]')) {
      e.preventDefault()
      e.stopPropagation()
      // The roster may not be held by any surface on this page; the composable
      // fetches it on demand, so the chord works off a trace view too.
      switcher.attachByNumber(Number(e.key))
      return
    }
    // A bare `/` must stay literal wherever a caret is, while the palette
    // already owns the keyboard, and under any open dialog. Only the ⌘K chord
    // pierces presentation mode (spec §07); `/` is a FEED-view affordance, and
    // the reader documents `/` as one of the single keys it must swallow
    // rather than let through to the page it is covering.
    if (e.key === '/' && !mod && !search.open.value && !isTyping(e.target)
        && !document.querySelector('[role="dialog"]')
        && SLASH_ROUTES.some(prefix => window.location.pathname.startsWith(prefix))) {
      e.preventDefault()
      search.openPalette()
    }
  }

  onMounted(() => window.addEventListener('keydown', onKeydown, true))
  onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown, true))

  return {
    openSearch: () => search.openPalette(),
    closeSearch: () => { if (search.open.value) search.closePalette() },
  }
}
