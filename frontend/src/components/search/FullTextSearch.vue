<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { FocusScope } from 'reka-ui'
import SearchResultList from './SearchResultList.vue'
import SearchPreview from './SearchPreview.vue'
import SessionSwitcherList from './SessionSwitcherList.vue'
import SessionSwitcherPreview from './SessionSwitcherPreview.vue'
import { useFullTextSearch } from '../../composables/useFullTextSearch.js'
import { useSessionSwitcher } from '../../composables/useSessionSwitcher.js'
import { useMediaQuery } from '../../composables/useMediaQuery.js'
import { useQuickCapture } from '../../composables/useQuickCapture.js'
import { MIN_ID_CHARS, NARROW_MAX_PX, SCOPES } from '../../utils/searchDocs.js'

const router = useRouter()
const search = useFullTextSearch()
const capture = useQuickCapture()
// One shell, two payloads (spec §01): when the query is empty on the Sessions
// facet, or is a `#<id>` address, the ROW BODY, the PREVIEW BODY and the
// FOOTER ACTION swap — the geometry, the focus trap, the facet bar and the key
// handling stay exactly as they are.
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

const switcherRows = computed(() => (lookupMode.value
  ? switcher.lookupRows.value
  : switcher.rows.value))

watch(() => search.open.value && search.idPrefix.value, (prefix) => {
  switcher.lookup(prefix === false ? null : prefix)
}, { immediate: true })

// Three different empty lists, three different reasons — an address that is
// still being typed, one that resolved to nothing, and a roster with nothing
// in it.
const switcherEmpty = computed(() => {
  if (!lookupMode.value) return undefined
  const prefix = search.idPrefix.value
  if (prefix.length < MIN_ID_CHARS) {
    return `Keep typing — ${MIN_ID_CHARS} or more characters of a session id.`
  }
  return `No session id starts with ${prefix}.`
})

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
  return n === 1 ? '1 match' : `${n} matches`
})

const activeDescendant = computed(() => {
  const id = switcherMode.value ? switcherId.value : search.activeId.value
  return id ? `search-row-${id}` : undefined
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

const facets = computed(() => search.facets.value.map(f => ({
  ...f,
  active: f.label === search.activeFacet.value,
  // In the switcher the Sessions chip counts the ROSTER, not the search
  // corpus: with an empty query the corpus is empty by definition, so the chip
  // would read 0 beside a list of twelve rows.
  count: (switcherMode.value && f.kind === 'session')
    ? switcherRows.value.length
    : (search.counts.value[f.kind ?? 'All'] ?? 0),
})))

const scopes = computed(() => SCOPES.map(s => ({
  ...s,
  active: s.value === search.activeScope.value,
  // The segment is not merely inert without a trace — it has no referent, and
  // saying so is what stops "0 in this session" from reading as "nothing here".
  disabled: s.value === 'session' && !search.sessionAvailable.value,
})))

// The header quotes the same number the active chip does. It used to count the
// RENDERED rows, which are capped per group and filtered by facet, so it
// contradicted the chip beside it the moment a group overflowed.
const activeKind = computed(() => search.facets.value
  .find(f => f.label === search.activeFacet.value)?.kind ?? 'All')

const resultCount = computed(() => {
  if (search.held.value) return 'Everything indexed'
  const n = search.counts.value[activeKind.value] ?? 0
  return `${n} ${n === 1 ? 'result' : 'results'}`
})

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
  ? 'Search this session — spans, diffs, output, files touched…'
  : 'Search every session, wiki page and touched file…'))

const noMatches = computed(() =>
  !search.held.value && !search.remotePending.value && !search.flatRows.value.length)

watch(() => search.open.value, async (isOpen) => {
  if (!isOpen) { sheetOpen.value = false; return }
  await nextTick()
  inputEl.value?.focus()
  inputEl.value?.select()
})

function scrollActiveIntoView(explicitId = null) {
  nextTick(() => {
    const id = explicitId ?? search.activeId.value
    if (!id) return
    document.getElementById(`search-row-${id}`)?.scrollIntoView({ block: 'nearest' })
  })
}

function select(row) {
  search.activeId.value = row.doc.id
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

function move(delta) {
  if (switcherMode.value) { moveSwitcher(delta); return }
  search.moveActive(delta)
  scrollActiveIntoView()
}

// Escape peels one layer: the sheet if it is covering the list, else the
// palette itself.
function closeTopLayer() {
  if (narrow.value && sheetOpen.value) { sheetOpen.value = false; return }
  search.closePalette()
}

// Where the preview is a sheet, ↵ has to reveal it before it can open
// anything — otherwise the reader never sees what they are committing to.
function openOrReveal() {
  if (narrow.value && !sheetOpen.value) { sheetOpen.value = true; return }
  // ↵ is `switch-client`, not `open`: in the Sessions facet it attaches.
  if (switcherMode.value) { attach(switcherRow.value); return }
  openRow(search.activeRow.value)
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
  const chord = e.ctrlKey && CTRL_KEYS[e.key.toLowerCase()]
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
  // 1–9 attach by number, but ONLY in the roster — where the query is empty
  // by definition and the digit would otherwise begin a filter that starts
  // with a numeral. Under `#` the query is a trace id, which is mostly digits,
  // so the hotkey has to give them back or the address can never be typed. That trade is deliberate: a hotkey you can reach beats a
  // filter term almost nobody types, and one keystroke on the query row
  // (any non-digit) leaves the switcher and gives the digits back.
  if (switcherMode.value && !lookupMode.value
      && !e.metaKey && !e.ctrlKey && !e.altKey
      && /^[1-9]$/.test(e.key)) {
    if (attachByNumber(Number(e.key))) { e.preventDefault(); return }
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
                ? 'Search sessions, or #session-id to jump straight to one…'
                : placeholder"
              spellcheck="false"
              autocomplete="off"
              @input="search.setQuery($event.target.value)"
            >
            <span class="query-count">{{ switcherMode ? switcherCount : resultCount }}</span>
            <button
              v-if="search.trimmed.value"
              type="button"
              class="query-clear"
              aria-label="Clear query"
              @click="clearQuery"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18" /></svg>
            </button>
            <button type="button" class="query-esc" @click="search.closePalette()">esc</button>
          </div>

          <div class="facet-bar">
            <div class="scope-group" role="group" aria-label="Search scope">
              <button
                v-for="s in scopes"
                :key="s.value"
                type="button"
                class="scope"
                :class="{ 'is-active': s.active }"
                :aria-pressed="s.active"
                :disabled="s.disabled"
                :title="s.disabled ? 'Open a session trace to search inside it' : undefined"
                @click="search.setScope(s.value)"
              >{{ s.label }}</button>
            </div>
            <span class="filter-sep" aria-hidden="true" />
            <button
              v-for="f in facets"
              :key="f.label"
              type="button"
              class="facet"
              :class="{ 'is-active': f.active }"
              :aria-pressed="f.active"
              @click="search.setFacet(f.label)"
            >{{ f.label }}<span class="facet-count">{{ f.count }}</span></button>
            <span v-if="!lookupMode" class="facet-hint">tab cycles kind</span>
          </div>

          <div class="search-panes" :class="{ 'is-sheet': narrow && sheetOpen }">
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
              :empty-text="switcherEmpty"
              :show-hotkeys="!lookupMode"
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
              :query="search.trimmed.value"
              :active-id="search.activeId.value"
              :recents="search.recents.value"
              :show-recents="search.held.value"
              :no-matches="noMatches"
              :can-widen="canWiden"
              @select="select"
              @open="openRow"
              @retry="search.retry()"
              @recent="useRecent"
              @widen="widen"
            />
            <div v-if="narrow && sheetOpen" class="sheet-back">
              <button type="button" @click="sheetOpen = false">← results</button>
            </div>
            <SessionSwitcherPreview
              v-if="switcherMode"
              :row="switcherRow"
              :clock="switcher.clock.value"
              @attach="attach"
            />
            <SearchPreview
              v-else
              :row="search.activeRow.value"
              :query="search.trimmed.value"
              @open="openRow"
            />
          </div>

          <div class="hint-bar">
            <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
            <span v-if="switcherMode"><kbd>↵</kbd> attach</span>
            <span v-else><kbd>↵</kbd> open</span>
            <span v-if="switcherMode && !lookupMode"><kbd>1</kbd>–<kbd>9</kbd> jump</span>
            <span v-if="!lookupMode"><kbd>tab</kbd> kind</span>
            <span v-if="!switcherMode"><kbd>ctrl</kbd><kbd>s</kbd> scope</span>
            <span><kbd>ctrl</kbd><kbd>i</kbd> new idea</span>
            <span><kbd>esc</kbd> close</span>
            <span class="hint-spans">{{ switcherMode ? switcherCount : scopeHint }}</span>
          </div>

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

.search-panel {
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
  gap: 0.75rem;
  padding: 1rem 1.125rem 0.875rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
}

.query-glyph { width: 20px; height: 20px; color: var(--color-fg-subtle); flex-shrink: 0; }

.query-input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  font-size: 1.25rem;
  font-weight: 500;
  color: var(--color-fg);
  padding: 0;
}

.query-input::placeholder { color: var(--color-fg-subtle); }

.query-count {
  flex-shrink: 0;
  font-size: 0.71875rem;
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
  font-size: 0.6875rem;
  color: var(--color-fg-muted);
  background: var(--color-background);
  border: 1px solid var(--color-border);
  border-radius: 7px;
  padding: 4px 9px;
  cursor: pointer;
}

.facet-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 0.4375rem;
  padding: 0.6875rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-background);
  overflow-x: auto;
}

.facet {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.3125rem 0.6875rem;
  border-radius: 9px;
  cursor: pointer;
  background: var(--color-surface);
  color: var(--color-fg-muted);
  border: 1px solid var(--color-border);
  white-space: nowrap;
}

.facet.is-active {
  background: var(--color-fg);
  color: var(--color-surface);
  border-color: var(--color-fg);
}

.scope-group {
  display: inline-flex;
  flex-shrink: 0;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 9px;
  padding: 2px;
  gap: 2px;
}

.scope {
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.1875rem 0.5625rem;
  border-radius: 7px;
  border: 0;
  background: transparent;
  color: var(--color-fg-muted);
  cursor: pointer;
  white-space: nowrap;
}

.scope.is-active { background: var(--color-info-soft); color: var(--color-info-strong); }

.scope:disabled { opacity: 0.45; cursor: not-allowed; }

.filter-sep {
  flex-shrink: 0;
  width: 1px;
  align-self: stretch;
  margin: 0 0.1875rem;
  background: var(--color-border);
}

.facet-count { font-size: 0.65625rem; color: var(--color-fg-subtle); font-variant-numeric: tabular-nums; }

.facet.is-active .facet-count { color: var(--color-surface); opacity: 0.62; }

.facet-hint {
  margin-left: auto;
  font-size: 0.6875rem;
  color: var(--color-fg-subtle);
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  white-space: nowrap;
}

.search-panes { flex: 1; min-height: 0; display: flex; position: relative; }

.hint-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.625rem 1.25rem;
  border-top: 1px solid var(--color-border);
  background: var(--color-background);
  font-size: 0.6875rem;
  color: var(--color-fg-subtle);
}

.hint-bar span { display: inline-flex; align-items: center; gap: 0.25rem; }

.hint-spans { margin-left: auto; font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace); }

kbd {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.625rem;
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
  /* The preview stops being a column and becomes a push-over sheet the
     result list slides out from under. */
  .search-panes > :deep(.search-preview) { display: none; }
  .search-panes.is-sheet > :deep(.search-results) { display: none; }
  .search-panes.is-sheet > :deep(.search-preview) { display: flex; padding-top: 2.25rem; }
}

/* The keyboard legends are the only content here that cannot shrink, so they
   set the panel's minimum width and push it into horizontal overflow on a
   phone — where they are also advice for keys the reader does not have. The
   count stays: it is the one line that still means something by touch. */
@media (max-width: 560px) {
  .hint-bar span:not(.hint-spans) { display: none; }
  .hint-spans { margin-left: 0; }
  .facet-hint { display: none; }
}
</style>
