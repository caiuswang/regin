<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { FocusScope } from 'reka-ui'
import SearchFacetRail from './SearchFacetRail.vue'
import SearchResultList from './SearchResultList.vue'
import SearchPreview from './SearchPreview.vue'
import SessionSwitcherList from './SessionSwitcherList.vue'
import SessionSwitcherPreview from './SessionSwitcherPreview.vue'
import { useFullTextSearch } from '../../composables/useFullTextSearch.js'
import { useSessionSwitcher } from '../../composables/useSessionSwitcher.js'
import { useMediaQuery } from '../../composables/useMediaQuery.js'
import { useQuickCapture } from '../../composables/useQuickCapture.js'
import { trackWalkOrigin, useEntityWalk } from '../../composables/useEntityWalk.js'
import { useCopy } from '../../composables/useCopy.js'
import { openFilePreview } from '../../composables/useFilePreview.js'
import {
  MIN_ID_CHARS, NARROW_MAX_PX, SCOPES, caretAtEnd, hitLines, lookupNote, railFacet, rowDomId,
} from '../../utils/searchDocs.js'

const router = useRouter()
const search = useFullTextSearch()
const capture = useQuickCapture()
// One shell, two payloads (spec §01): when the query is empty on the Sessions
// facet, or is a `#<id>` address, the ROW BODY, the PREVIEW BODY and the
// FOOTER ACTION swap — the geometry, the focus trap, the facet rail and the
// key handling stay exactly as they are.
const switcher = useSessionSwitcher()
const switcherMode = search.switcherMode
// Held separately from `search.activeId`, which is keyed on search-document
// ids; a session row is not a document and the two lists must not fight over
// one cursor. Local to this component, which never unmounts, so the selection
// survives a close.
const switcherId = ref(null)

// `#<prefix>` addresses a session by id instead of searching a corpus. The
// roster is twelve rows and the trace view holds a paginated window, so the
// answer can only come from the server — the lookup leg below is that request.
const lookupMode = computed(() => search.idPrefix.value !== null)
// No rail (R1) and its own key legend — only under ⌘J. A `#` address, or the
// Sessions kind picked in ⌘K, is a search state and keeps the rail.
const rosterMode = search.rosterMode

const switcherRows = computed(() => (lookupMode.value
  ? switcher.lookupRows.value
  : switcher.rows.value))

watch(() => search.open.value && search.idPrefix.value, (prefix) => {
  switcher.lookup(prefix === false ? null : prefix)
}, { immediate: true })

// Three different empty lists, three different reasons — an address that is
// still being typed, one that resolved to nothing, and a roster with nothing
// in it (the list's own default).
const switcherEmpty = computed(() => (lookupMode.value ? lookupNote(search.idPrefix.value) : {}))

const switcherRow = computed(() =>
  switcherRows.value.find(r => r.trace_id === switcherId.value)
  || switcherRows.value[0] || null)

const attachedId = computed(() => search.source.value?.traceId || null)

// The roster is only worth polling while it is on screen.
watch(() => search.open.value && switcherMode.value, (on) => {
  // `tail`: this facet is the only surface that renders the preview chat.
  if (on) switcher.hold({ tail: true })
  else switcher.release()
}, { immediate: true })

// A row that left the roster must not strand the cursor on an id nothing
// renders — re-pin to the top rather than to a ghost.
watch(switcherRows, (rows) => {
  if (!rows.length) { switcherId.value = null; return }
  if (!rows.some(r => r.trace_id === switcherId.value)) {
    switcherId.value = rows[0].trace_id
  }
})

function moveSwitcher(delta) {
  const rows = switcherRows.value
  if (!rows.length) return
  const at = rows.findIndex(r => r.trace_id === switcherId.value)
  const next = (at < 0 ? 0 : (at + delta + rows.length) % rows.length)
  switcherId.value = rows[next].trace_id
  scrollActiveIntoView(rows[next].trace_id)
}

function attach(row) {
  if (!row) return
  search.closePalette()
  switcher.attach(row)
}

function attachByNumber(n) {
  const row = switcherRows.value[n - 1]
  if (!row) return false
  attach(row)
  return true
}

function selectSwitcher(row) {
  switcherId.value = row.trace_id
  // Same rule the search list follows: below the split the preview is a sheet,
  // and a pointer picking a row IS the request to read it.
  if (narrow.value) sheetOpen.value = true
}

const switcherCount = computed(() => {
  const n = switcherRows.value.length
  if (!lookupMode.value) return `${n} ${n === 1 ? 'session' : 'sessions'}`
  if (search.idPrefix.value.length < MIN_ID_CHARS) return 'keep typing'
  return n === 1 ? '1 match' : `${n} matches`
})

const activeDescendant = computed(() => {
  if (switcherMode.value) return switcherId.value ? `search-row-${switcherId.value}` : undefined
  return search.activeId.value ? rowDomId(search.activeId.value) : undefined
})

// Which hit inside the highlighted result the preview is parked on, and
// whether that pane is reading the document or listing its hits. Held here
// rather than in the preview because both are driven by keys the palette
// traps — the preview never sees a keystroke.
const hitIndex = ref(0)
const previewMode = ref('read')

// Axis 2 (preview focus), independent of the list cursor: the entity the
// right pane shows, and the relations walked from the highlighted row.
const walk = useEntityWalk()
const { copyText, isCopied } = useCopy()
const entityStack = walk.stack
const walking = computed(() => entityStack.value.length > 0)
trackWalkOrigin(walk, {
  open: search.open, roster: switcherMode, rosterRow: switcherRow, activeRow: search.activeRow,
  clock: switcher.clock,
})

const previewHits = computed(() => {
  const row = switcherMode.value ? null : search.activeRow.value
  return row ? hitLines(row.doc.body, search.trimmed.value) : []
})

// Landing on a new row means landing on ITS first hit; carrying the index
// over would park the stepper at hit #31 of a document with four.
watch(() => search.activeId.value, () => { hitIndex.value = 0 })
// A query that no longer matches leaves nothing to step through, and the
// hit list with nothing in it is a worse answer than the document.
watch(previewHits, (hits) => {
  if (!hits.length) { hitIndex.value = 0; previewMode.value = 'read' }
  else if (hitIndex.value >= hits.length) hitIndex.value = 0
})

function pickOrStep(delta) {
  if (walking.value) walk.movePick(delta)
  else stepHit(delta)
}

function stepHit(delta) {
  const n = previewHits.value.length
  if (!n) return
  hitIndex.value = (hitIndex.value + delta + n) % n
}

// Picking a line in the hit list is a request to READ there, not to stay in
// the list — the list answered "which one", and the answer to that is the
// document open at it.
function pickHit(i) {
  hitIndex.value = i
  previewMode.value = 'read'
}

function togglePreviewMode() {
  if (!previewHits.value.length) return
  previewMode.value = previewMode.value === 'read' ? 'hits' : 'read'
}

const ROSTER_LEGEND = [
  { key: '↑↓', label: 'move' },
  { key: '1–9', label: 'attach' },
  { key: '#', label: 'address' },
  { key: '↵', label: 'attach' },
  { key: '⌘K', label: 'search' },
]

const railLegend = computed(() => {
  const out = [{ keys: ['↑', '↓'], label: 'move' }]
  if (!lookupMode.value) out.push({ keys: ['⇥'], label: 'next kind' })
  out.push({ keys: ['↵'], label: switcherMode.value ? 'attach' : 'open' })
  if (walking.value) {
    out.push({ keys: ['⌘', '↵'], label: 'secondary' })
    out.push({ keys: ['⌥', '↑↓'], label: 'pick relation' })
    out.push({ keys: ['⌥', '→'], label: 'follow' })
    const prev = walk.stack.value[walk.stack.value.length - 2]
    if (prev) out.push({ keys: ['⌥', '←'], label: `back to ${prev.ref}` })
  } else if (previewHits.value.length) {
    out.push({ keys: ['⌥', '↑↓'], label: 'walk hits' })
    out.push({ keys: ['⌥', 'L'], label: 'hit list' })
  }
  out.push({ keys: ['⌃', 'I'], label: 'new idea' })
  return out
})

const inputEl = ref(null)
// Below NARROW_MAX_PX the preview stops being a column and becomes a
// push-over sheet reached by ↵. The query below and the `@media` block at
// the bottom of this file MUST name the same number — a JS/CSS split there
// gives a viewport where ↵ opens a sheet that CSS is still hiding. Tailwind's
// md (768) and lg (1024) both straddle it, so neither useBreakpoint flag can
// stand in.
const narrow = useMediaQuery(`(max-width: ${NARROW_MAX_PX}px)`)
const sheetOpen = ref(false)

const facets = computed(() => search.facetView.value.map(f => railFacet(f, {
  switcher: switcherMode.value,
  held: search.held.value,
  rosterCount: switcherRows.value.length,
  activeLabel: search.activeFacet.value,
})))

const scopes = computed(() => SCOPES.map(s => ({
  ...s,
  active: s.value === search.activeScope.value,
  // The segment is not merely inert without a trace — it has no referent, and
  // saying so is what stops "0 in this session" from reading as "nothing here".
  disabled: s.value === 'session' && !search.sessionAvailable.value,
})))

// The header quotes the same number the active chip does (`group.total`), never
// the RENDERED rows, which are capped per group.
const resultCount = search.headerCount

// `counts.span` counts MATCHING spans, so it cannot be phrased as a fact
// about the session — "0 spans in this session" inside a 900-span trace is
// simply false. It only means anything once there is a query to match.
const scopeHint = computed(() => {
  if (search.activeScope.value !== 'session') return 'searching every session'
  if (search.held.value) return 'searching this session'
  const n = search.counts.value.span ?? 0
  return `${n} matching ${n === 1 ? 'span' : 'spans'} here`
})

const canWiden = computed(() =>
  search.activeScope.value === 'session' && search.sessionAvailable.value)

const placeholder = computed(() => (search.activeScope.value === 'session'
  ? 'Search this session’s spans and files…'
  : 'Search features, tasks, sessions… or F146 / T419 / #id'))

const noMatches = search.noMatches

watch(() => search.open.value, async (isOpen) => {
  if (!isOpen) { sheetOpen.value = false; return }
  await nextTick()
  inputEl.value?.focus()
  inputEl.value?.select()
})

function scrollActiveIntoView(explicitId = null) {
  nextTick(() => {
    const domId = explicitId ? `search-row-${explicitId}` : rowDomId(search.activeId.value)
    if (!explicitId && !search.activeId.value) return
    document.getElementById(domId)?.scrollIntoView({ block: 'nearest' })
  })
}

function select(row) {
  search.activeId.value = row.key
  // On a narrow viewport the preview is a sheet the list slides out from
  // under, and Enter was the only way in — leaving a tap on a phone with
  // nothing to show for it. A pointer selecting a row IS the request to read
  // it.
  if (narrow.value) sheetOpen.value = true
}

// One verb per kind, no ambiguity. A span hands off to the trace that owns
// it — which knows how to reveal a folded row and scope into a subagent —
// while every other kind is a route.
function openRow(row) {
  if (!row) return
  const doc = row.doc
  const query = search.trimmed.value
  search.rememberQuery(query)
  search.closePalette()
  if (doc.kind === 'span' && search.source.value?.openSpan) {
    // The query travels with the hit: the preview marks the matched term, and
    // the reader you land in is where you actually need to find it.
    search.source.value.openSpan(doc.id, query)
    return
  }
  if (doc.href) router.push(doc.href)
}

// The drawer sits at --z-modal, under the palette's --z-search, so the
// palette has to go first or the file opens out of sight.
function openFile(source, anchor) {
  if (!source) return
  search.rememberQuery(search.trimmed.value)
  search.closePalette()
  openFilePreview(source, anchor)
}

function move(delta) {
  if (switcherMode.value) { moveSwitcher(delta); return }
  search.moveActive(delta)
  scrollActiveIntoView()
}

// Escape peels one layer (R8): the sheet covering the list, then one step of
// the walk, then the Drill, then the query, and only then the palette itself.
function closeTopLayer() {
  if (narrow.value && sheetOpen.value) { sheetOpen.value = false; return }
  if (walk.back()) return
  if (search.inDrill.value) { search.leaveDrill(); return }
  if (search.trimmed.value) { clearQuery(); return }
  search.closePalette()
}

// The cursor can rest on a row, a "Show all N" row, or the Drill's load row;
// ↵ and a click do the one thing each of them is for.
function activate(entry) {
  if (!entry) return
  if (entry.type === 'more' && entry.home) showAllHome(entry)
  else if (entry.type === 'more') search.drillInto(entry.kind)
  else if (entry.type === 'doc') openRow(entry)
  else search.loadMore()
}

// A Home group's "Show all" pages every entry behind that group; the
// Sessions group's is every session, since the desk only held back the
// ones it had not shown above.
function showAllHome(entry) {
  search.showHomeList(entry.home === 'sessions' ? 'session' : entry.home)
  inputEl.value?.focus()
}

// R6. Every action is a route change, a pivot inside the preview, or a
// clipboard copy — the palette changes no state, so none of them posts.
function runAction(action) {
  if (!action || action.disabled) return
  if (action.type === 'pivot') { walk.push(action.target); return }
  if (action.type === 'copy') { copyText(action.text); return }
  search.rememberQuery(search.trimmed.value)
  if (action.type === 'tab') { window.open(action.href, '_blank', 'noopener'); return }
  search.closePalette()
  router.push(action.href)
}

// Unpivoted, ↵ keeps the row's own verb (a resolver's session row
// re-addresses through `#`); once walked, it acts on the focused entity.
function act(which) {
  if (which === 'primary' && !walk.pivoted.value) {
    if (switcherMode.value) attach(switcherRow.value)
    else openRow(search.activeRow.value)
    return
  }
  runAction(walk.actions.value?.[which])
}

function followPicked(key) {
  runAction(walk.follow(key))
}

// Where the preview is a sheet, ↵ has to reveal it before it can open
// anything — otherwise the reader never sees what they are committing to.
function openOrReveal() {
  // ↵ is `switch-client`, not `open`: in the Sessions facet it attaches.
  if (revealSheet()) return
  if (switcherMode.value || walk.pivoted.value) { act('primary'); return }
  activate(search.activeEntry.value)
}

// Below the split, ↵ and → both open the preview sheet before anything else (R8).
// Only a row has a preview; a "Show all" or load row acts straight away.
function revealSheet() {
  if (!narrow.value || sheetOpen.value) return false
  const previewable = switcherMode.value
    ? !!switcherRow.value
    : search.activeEntry.value?.type === 'doc'
  if (previewable) sheetOpen.value = true
  return previewable
}

// → is a caret key in the query, so it only opens the sheet once the caret
// has nothing left to its right.
const hasModifier = e => e.shiftKey || e.metaKey || e.ctrlKey || e.altKey

function arrowRight(e) {
  return caretAtEnd(e.target) && revealSheet()
}

// Capture an idea without searching for one. It lives here rather than on a
// global chord because every free ⌘-letter is spoken for by some browser
// (⌘I is Firefox's Page Info, Safari's Email This Page) — the palette already
// owns its keyboard and stops propagation, so a chord inside it conflicts
// with nothing. The palette closes first: two trapped focus scopes at once
// leaves the dialog beneath un-typeable.
function captureIdea() {
  search.closePalette()
  capture.open.value = true
}

// Ctrl chords. `n`/`p` are the emacs moves; `s` is the scope toggle, which
// needs a chord because Tab is spoken for by the kind chips and the palette
// traps focus — without one the scope control would be pointer-only.
const CTRL_KEYS = {
  n: () => move(1),
  p: () => move(-1),
  s: () => search.toggleScope(),
  i: captureIdea,
}

const PLAIN_KEYS = {
  ArrowDown: () => move(1),
  ArrowUp: () => move(-1),
  Enter: openOrReveal,
}

// ⌥ walks INSIDE the highlighted result; bare ↑↓ walks between results. The
// two axes were collapsed onto one pair of keys before, which is why reading
// a page with eighty hits meant leaving the palette.
const ALT_KEYS = {
  arrowdown: () => pickOrStep(1),
  arrowup: () => pickOrStep(-1),
  arrowright: () => { if (walking.value) followPicked() },
  arrowleft: () => walk.back(),
  l: togglePreviewMode,
}

// ⌃ and ⌥ are two chord tables over one modifier test, so which table a
// keystroke belongs to is decided here rather than inline in the dispatcher
// — six `&&` clauses spread across two branches is what pushed `onKeydown`
// past the complexity gate.
function chordFor(e) {
  if (e.metaKey) return e.key === 'Enter' && walking.value ? () => act('secondary') : undefined
  if (e.ctrlKey && !e.altKey) return CTRL_KEYS[e.key.toLowerCase()]
  // On macOS ⌥ composes a character, so the caller must preventDefault or
  // ⌥L drops a `¬` into the query.
  if (e.altKey && !e.ctrlKey) return ALT_KEYS[e.key.toLowerCase()]
  return undefined
}

// 1–9 attach by number, but ONLY in the roster — where the query is empty by
// definition and the digit would otherwise begin a filter that starts with a
// numeral. Under `#` the query is a trace id, which is mostly digits, so the
// hotkey has to give them back or the address can never be typed. That trade
// is deliberate: a hotkey you can reach beats a filter term almost nobody
// types, and one keystroke on the query row (any non-digit) leaves the
// switcher and gives the digits back.
function isRosterDigit(e) {
  if (!switcherMode.value || lookupMode.value) return false
  if (e.metaKey || e.ctrlKey || e.altKey) return false
  return /^[1-9]$/.test(e.key)
}

function onKeydown(e) {
  if (e.key === 'Escape') {
    e.preventDefault()
    // stopPropagation, not just preventDefault: presentation mode's dialog
    // sits underneath and dismisses on Escape. Leaving this to its
    // `defaultPrevented` guard made "esc closes the palette, not the reader"
    // an accident of handler order rather than a rule.
    e.stopPropagation()
    closeTopLayer()
    return
  }
  const chord = chordFor(e)
  if (chord) {
    e.preventDefault()
    chord()
    return
  }
  if (e.key === 'Tab') {
    e.preventDefault()
    search.cycleFacet(e.shiftKey ? -1 : 1)
    return
  }
  if (e.key === 'Backspace' && !search.query.value && search.inDrill.value) {
    e.preventDefault()
    search.leaveDrill()
    return
  }
  if (isRosterDigit(e) && attachByNumber(Number(e.key))) {
    e.preventDefault()
    return
  }
  if (e.key === 'ArrowRight' && !hasModifier(e) && arrowRight(e)) {
    e.preventDefault()
    return
  }
  const plain = PLAIN_KEYS[e.key]
  if (!plain) return
  e.preventDefault()
  plain()
}

function clearQuery() {
  search.setQuery('')
  inputEl.value?.focus()
}

function useRecent(term) {
  search.setQuery(term)
  inputEl.value?.focus()
}

function widen() {
  search.setScope('global')
  inputEl.value?.focus()
}
</script>

<template>
  <Teleport to="body">
    <Transition name="search-fade">
      <div
        v-if="search.open.value"
        class="search-backdrop"
        @mousedown.self="search.closePalette()"
      >
        <!--
          A trapped FocusScope, not a bare div, because presentation mode is a
          Reka dialog whose own scope would otherwise yank the caret straight
          back out of the input below. Reka keeps a STACK of focus scopes
          (reka-ui/FocusScope/stack.js): mounting one pauses the scope beneath
          it and unmounting resumes it, which is exactly "the palette stacks
          over the reader and hands it back on esc". Joining that stack is the
          library's mechanism for it — an ad-hoc div cannot participate.
        -->
        <FocusScope
          as="div"
          trapped
          class="search-panel"
          role="dialog"
          aria-modal="true"
          aria-label="Full-text search"
          :data-search-palette="search.open.value ? '' : undefined"
          @keydown="onKeydown"
        >
          <div class="query-row">
            <svg class="query-glyph" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7.5" /><path d="m20.5 20.5-4-4" /></svg>
            <input
              ref="inputEl"
              :value="search.query.value"
              type="text"
              role="combobox"
              aria-expanded="true"
              aria-controls="search-listbox"
              :aria-activedescendant="activeDescendant"
              :aria-label="switcherMode ? 'Filter sessions' : 'Search'"
              class="query-input"
              :placeholder="switcherMode
                ? 'Filter sessions, or #id to jump straight to one…'
                : placeholder"
              spellcheck="false"
              autocomplete="off"
              @input="search.setQuery($event.target.value)"
            >
            <span class="query-count">{{ switcherMode ? switcherCount : resultCount }}</span>
            <button
              v-if="search.trimmed.value"
              type="button"
              class="query-clear focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Clear query"
              @click="clearQuery"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18" /></svg>
            </button>
            <button
              type="button"
              class="query-esc focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              @click="search.closePalette()"
            >esc</button>
          </div>
          <p
            v-if="search.scopeNotice.value"
            class="scope-notice"
            role="status"
            data-testid="scope-notice"
          >{{ search.scopeNotice.value }}</p>

          <div
            class="search-panes"
            :class="{ 'is-sheet': narrow && sheetOpen, 'is-pivoted': walk.pivoted.value }"
            :data-pivoted="walk.pivoted.value ? '' : undefined"
          >
            <SearchFacetRail
              v-if="!rosterMode"
              :scopes="scopes"
              :facets="facets"
              :counted="!search.held.value"
              :recents="search.held.value ? search.recents.value : []"
              :legend="railLegend"
              @scope="search.setScope"
              @facet="search.setFacet"
              @recent="useRecent"
            />
            <SessionSwitcherList
              v-if="switcherMode"
              id="search-listbox"
              role="listbox"
              aria-label="Sessions"
              :rows="switcherRows"
              :active-id="switcherId"
              :attached-id="attachedId"
              :clock="switcher.clock.value"
              :loading="lookupMode ? switcher.lookupLoading.value : switcher.loading.value"
              :error="lookupMode ? switcher.lookupError.value : switcher.error.value"
              :empty-text="switcherEmpty.body"
              :empty-title="switcherEmpty.title"
              :show-hotkeys="!lookupMode"
              :walked="walk.pivoted.value"
              :loading-text="lookupMode ? 'resolving that id…' : undefined"
              @select="selectSwitcher"
              @attach="attach"
            />
            <SearchResultList
              v-else
              id="search-listbox"
              role="listbox"
              aria-label="Search results"
              :groups="search.groups.value"
              :drill="search.drillView.value"
              :address="search.addressView.value"
              :home="search.homeView.value"
              :query="search.trimmed.value"
              :active-id="search.activeId.value"
              :no-matches="noMatches"
              :offline="search.offline.value"
              :held="search.held.value && !search.homeView.value"
              :held-label="search.activeFacet.value === 'All' ? '' : search.activeFacet.value"
              :can-widen="canWiden"
              @select="select"
              @open="openRow"
              @activate="activate"
              @retry="search.retryKind"
              @retry-all="search.retry()"
              @leave-drill="search.leaveDrill()"
              @widen="widen"
            />
            <div v-if="narrow && sheetOpen" class="sheet-back">
              <button
                type="button"
                class="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                @click="sheetOpen = false"
              >← results</button>
            </div>
            <SessionSwitcherPreview
              v-if="switcherMode"
              :stack="entityStack"
              :pick-key="walk.pickKey.value"
              :actions="walk.actions.value"
              :copied="!!walk.actions.value && isCopied(walk.actions.value.copy.text)"
              @follow="followPicked"
              @pop-to="walk.popTo"
              @act="act"
              @go="href => runAction({ type: 'route', href })"
              @retry="walk.retry()"
            />
            <SearchPreview
              v-else
              :row="search.activeRow.value"
              :query="search.trimmed.value"
              :hit-index="hitIndex"
              :mode="previewMode"
              @open="openRow"
              @open-file="openFile"
              @step="stepHit"
              @pick-hit="pickHit"
              @mode="m => previewMode = m"
              :entity-stack="entityStack"
              :pick-key="walk.pickKey.value"
              :actions="walk.actions.value"
              :copied="!!walk.actions.value && isCopied(walk.actions.value.copy.text)"
              @follow="followPicked"
              @pop-to="walk.popTo"
              @act="act"
              @go="href => runAction({ type: 'route', href })"
              @retry="walk.retry()"
            />
          </div>

          <footer v-if="rosterMode" class="roster-foot" aria-label="Roster keys">
            <span v-for="hint in ROSTER_LEGEND" :key="hint.label" class="roster-hint">
              <kbd>{{ hint.key }}</kbd>{{ hint.label }}
            </span>
            <span class="roster-hint is-end"><kbd>esc</kbd>close roster</span>
          </footer>

          <p class="sr-only" role="status" aria-live="polite">
            <template v-if="switcherMode">{{ switcherCount }}</template>
            <template v-else>{{ noMatches ? 'No matches' : resultCount }}</template>
          </p>
        </FocusScope>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.search-backdrop {
  position: fixed;
  inset: 0;
  z-index: var(--z-search);
  background: rgba(var(--scrim-rgb), 0.5);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 56px 24px 24px;
}

/* ============================================================
   TYPE SCALE — six steps, and nothing inside the palette may use
   a size that is not one of them. Before this pass there were
   eleven (9.5 / 10 / 10.5 / 11 / 11.5 / 12 / 12.5 / 13 / 13.5 /
   17 / 19px), which is why neighbouring labels read as accidents
   rather than as a hierarchy.

   Declared on the panel, not on :root — the palette is one
   surface with its own density, and the rest of the app has no
   business inheriting it. Custom properties cascade to every
   descendant regardless of scoped-CSS attributes, so the child
   components pick these up without importing anything.

   Monospace never gets its own px value: at equal px it reads
   ~8% larger than the sans, so it takes 0.93em on the step it
   already inherits. One rule, not a second ladder.
   ============================================================ */
.search-panel {
  --sp-t-display: 1.125rem;   /* 18px — the preview title. One per pane.   */
  --sp-t-input: 1rem;         /* 16px — the query. The only other large.   */
  --sp-t-body: 0.8125rem;     /* 13px — prose, result titles, hit lines.   */
  --sp-t-meta: 0.75rem;       /* 12px — controls: facets, scope, buttons.  */
  --sp-t-micro: 0.6875rem;    /* 11px — counts, crumbs, kickers, legends.  */
  --sp-t-kbd: 0.625rem;       /* 10px — key caps only; they sit in micro.  */
  --sp-lh-display: 1.28;
  --sp-lh-body: 1.62;
  --sp-lh-micro: 1.4;

  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 1120px;
  height: min(700px, calc(100vh - 96px));
  background: var(--color-surface);
  border-radius: 20px;
  box-shadow: 0 40px 90px -20px rgba(2, 6, 23, 0.55);
  overflow: hidden;
}

.query-row {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.625rem;
  height: 3.5rem;
  padding: 0 0.875rem 0 1.125rem;
  border-bottom: 1px solid var(--color-border);
}

.query-glyph { width: 20px; height: 20px; color: var(--color-fg-subtle); flex-shrink: 0; }

.query-input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  font-size: var(--sp-t-input);
  line-height: 1.45;
  font-weight: 450;
  letter-spacing: -0.005em;
  color: var(--color-fg);
  padding: 0;
}

.query-input::placeholder { color: var(--color-fg-subtle); }

.query-count {
  flex-shrink: 0;
  font-size: var(--sp-t-micro);
  color: var(--color-fg-subtle);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.query-clear {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  border: 0;
  background: transparent;
  color: var(--color-fg-subtle);
  cursor: pointer;
  padding: 0;
}

.query-clear svg { width: 16px; height: 16px; }

.query-clear:hover { background: var(--color-surface-2); color: var(--color-fg); }

.query-esc {
  flex-shrink: 0;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-kbd);
  color: var(--color-fg-muted);
  background: var(--color-background);
  border: 1px solid var(--color-border);
  border-radius: 7px;
  padding: 4px 9px;
  cursor: pointer;
}












.search-panes { flex: 1; min-height: 0; display: flex; position: relative; }

.scope-notice {
  flex-shrink: 0;
  margin: 0;
  padding: 0.4375rem 1.125rem;
  font-size: var(--sp-t-meta);
  color: var(--color-warning-strong);
  background: var(--color-warning-soft);
  border-bottom: 1px solid var(--color-border);
}

/* While the preview is walked away from it, the origin row keeps a dashed
   outline: still the list's cursor, no longer what ↵ acts on. */
.search-panes.is-pivoted :deep(.result-row.is-active) {
  background: transparent;
  box-shadow: none;
  outline: 1.5px dashed var(--color-border-strong);
  outline-offset: -1.5px;
}



.roster-foot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.5rem 0.875rem;
  border-top: 1px solid var(--color-border);
  font-size: var(--sp-t-micro);
  color: var(--color-fg-subtle);
  white-space: nowrap;
  overflow: hidden;
}

.roster-hint { display: inline-flex; align-items: center; gap: 0.375rem; }

.roster-hint.is-end { margin-left: auto; }

kbd {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-kbd);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  padding: 1px 5px;
  border-radius: 4px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

.sheet-back {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 1;
  padding: 0.5rem 0.75rem;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}

.sheet-back button {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-info-strong);
  background: none;
  border: 0;
  cursor: pointer;
}

.search-fade-enter-active,
.search-fade-leave-active { transition: opacity 150ms; }

.search-fade-enter-from,
.search-fade-leave-to { opacity: 0; }

@media (max-width: 900px) {
  .search-backdrop { padding: 16px 12px 12px; }
  .search-panel { height: calc(100vh - 28px); border-radius: 16px; }
  /* The rail lies down (see SearchFacetRail), so the panes have to stack or
     a horizontal strip would sit in a column beside the results. */
  .search-panes { flex-direction: column; }
  /* The preview stops being a column and becomes a push-over sheet the
     result list slides out from under. */
  .search-panes > :deep(.search-preview) { display: none; }
  /* The sheet is the preview alone: the strip stays behind with the results,
     or it sits under the absolutely-placed back bar and pushes the preview
     down by its own height. */
  .search-panes.is-sheet > :deep(.search-results),
  .search-panes.is-sheet > :deep(.facet-rail) { display: none; }
  .search-panes.is-sheet > :deep(.search-preview) { display: flex; padding-top: 2.25rem; }
}

</style>
