// `@`-triggered file/directory autocomplete for the /live bridge composer.
//
// Mirrors useSlashCommands.js's shape (same exported surface, same keymap
// contract) so LiveComposer's wiring stays symmetric between the two menus.
// Differences that are inherent to mentions, not style choices:
//   - the trigger is anywhere in the draft, not just at message start, so the
//     token is found by scanning BACKWARDS from the caret;
//   - the catalog is a server-side *search* (the tree is unbounded), so every
//     query round-trips instead of filtering one cached list.
//
// Accepted text keeps the leading `@` — that is what Claude Code's file
// reference parser reads — and is never quoted: the bridge types this straight
// into a terminal, where a quote would be delivered literally.
import { ref, computed } from 'vue'
import api from '../api'

const DEBOUNCE_MS = 180
const LIMIT = 50

// The `@mention` the caret sits in, scanning backwards for the nearest `@`.
// Returns { start, end, query } or null. Three rejections keep this off
// ordinary prose: an `@` that is not at a word boundary (`foo@bar.com`, `"@`),
// a query that already ran past the mention (whitespace or a quote inside it,
// i.e. the caret moved on), and a query that is itself another `@` (`@@`, which
// is not a path the user is halfway through typing). An empty query is valid —
// it lists the root.
export function mentionToken(text, caret) {
  const pos = Math.min(Math.max(caret ?? 0, 0), text.length)
  for (let i = pos - 1; i >= 0; i--) {
    if (text[i] !== '@') continue
    if (i > 0 && !/\s/.test(text[i - 1])) continue
    const query = text.slice(i + 1, pos)
    if (/[\s"']/.test(query) || query.startsWith('@')) continue
    return { start: i, end: pos, query }
  }
  return null
}

export function useFileMentions() {
  const items = ref([])
  const open = ref(false)
  const query = ref('')
  const activeIndex = ref(0)
  const pending = ref(false)
  // Bumped when an armed accept (see `armed`) is ready to apply. The caller
  // owns the draft text, so it watches this and calls accept() itself.
  const armedResolved = ref(0)

  let sessionId = ''
  let seq = 0
  let timer = null
  let token = null
  // An Enter/Tab pressed while the list was stale, remembered as
  // { query, start } instead of dropped: when that exact query's rows land on
  // that exact token, the highlight is accepted on the user's behalf. Anything
  // else — a further keystroke, a close, an empty result — clears it.
  let armed = null
  // The query `items` actually answers. `null` until the first response lands,
  // and !== query.value for as long as a newer query is in flight.
  const listQuery = ref(null)

  const filtered = computed(() => (open.value ? items.value : []))
  // The rows on screen answer a query the user has already typed past. They
  // stay up (a menu that blanks on every keystroke is worse) but they are not
  // acceptable, so the menu has to say so rather than look live.
  const stale = computed(() => listQuery.value !== query.value)
  // Both halves of "these rows are not the answer yet": a request that has not
  // come back, and a response that landed for a superseded query. The second is
  // why this is not simply `pending`.
  const loading = computed(() => pending.value || stale.value)

  // Named for parity with useSlashCommands.ensureLoaded: there is no catalog to
  // prefetch, only the session whose tree subsequent queries search.
  function ensureLoaded(id) {
    if (id) sessionId = id
  }

  function clampIndex() {
    const n = filtered.value.length
    if (activeIndex.value > n - 1) activeIndex.value = Math.max(0, n - 1)
    if (activeIndex.value < 0) activeIndex.value = 0
  }

  async function run(q) {
    const mine = ++seq
    let rows = []
    try {
      const res = await api.get(
        `/sessions/${sessionId}/bridge-files?q=${encodeURIComponent(q)}&limit=${LIMIT}`)
      rows = (res && res.files) || []
    } catch { rows = [] }
    if (mine !== seq) return // a newer query already owns the list
    items.value = rows
    listQuery.value = q
    pending.value = false
    clampIndex()
    resolveArmed(q, rows.length)
  }

  // The armed accept only fires for the query and token it was armed on, and
  // only when that query actually produced rows — the safety property is
  // unchanged, a path the user did not choose is never inserted.
  function resolveArmed(q, count) {
    if (!armed) return
    const hit = armed.query === q && q === query.value
      && !!token && token.start === armed.start
    armed = null
    if (hit && count > 0) armedResolved.value++
  }

  // Debounced, so a fast typist issues one request per pause rather than one
  // per keystroke. `pending` flips immediately: the menu is already open, and a
  // 180ms window of "no file matches" would be a lie.
  function schedule(q) {
    if (timer) clearTimeout(timer)
    pending.value = true
    if (!sessionId) { pending.value = false; items.value = []; listQuery.value = q; return }
    timer = setTimeout(() => { timer = null; run(q) }, DEBOUNCE_MS)
  }

  function sync(text, caret) {
    const tok = mentionToken(text, caret)
    if (!tok) { close(); return }
    const changed = !open.value || tok.query !== query.value
    token = tok
    query.value = tok.query
    open.value = true
    if (changed) { armed = null; activeIndex.value = 0; schedule(tok.query) }
    clampIndex()
  }

  function move(delta) {
    const n = filtered.value.length
    if (n === 0) return
    activeIndex.value = (activeIndex.value + delta + n) % n
  }

  function setActive(i) { activeIndex.value = i }

  function close() {
    if (timer) { clearTimeout(timer); timer = null }
    seq++ // orphan any in-flight response
    open.value = false
    pending.value = false
    query.value = ''
    activeIndex.value = 0
    items.value = []
    token = null
    armed = null
    listQuery.value = null
  }

  // Replace the mention token with `@<path>`. A file completes the mention
  // (trailing space, menu closes); a directory drills in (`@<dir>/`, no space,
  // menu stays open on the children) — the same two-mode accept the terminal's
  // own picker has. Returns { text, caret } with the caret right after the
  // insert, so a mid-draft mention doesn't fling the cursor to the end.
  //
  // A stale list is never acceptable, by row or by key: the rows answer a query
  // the user has already typed past, so completing one would put a path they
  // never chose into a prompt that is one Cmd+Enter from a live agent. A key
  // pressed into that window is deferred (`armed`), never satisfied from the
  // rows that happen to be on screen.
  //
  // The insert is never quoted. A path with a space (`@docs/my notes.md`) is
  // therefore delivered as-is and the terminal reads only `@docs/my` — the same
  // limitation Claude Code's own `@` parser has, so there is no quoting that
  // would be more correct here.
  function accept(text, item) {
    if (stale.value) return null
    const chosen = item || filtered.value[activeIndex.value]
    if (!chosen || !token) return null
    const dir = chosen.kind === 'directory'
    const insert = dir ? `@${chosen.path}/` : `@${chosen.path} `
    const before = text.slice(0, token.start)
    const caret = before.length + insert.length
    const next = before + insert + text.slice(token.end)
    if (dir) {
      const q = `${chosen.path}/`
      token = { start: token.start, end: caret, query: q }
      query.value = q
      activeIndex.value = 0
      schedule(q)
    } else {
      close()
    }
    return { text: next, caret }
  }

  // Same contract as useSlashCommands.handleKeydown — Cmd/Ctrl+Enter is left
  // unhandled so the composer can still send with a menu open.
  function handleKeydown(e, text) {
    if (!open.value) return { handled: false }
    if (e.key === 'ArrowDown') { move(1); return { handled: true } }
    if (e.key === 'ArrowUp') { move(-1); return { handled: true } }
    if (e.key === 'Escape') { close(); return { handled: true } }
    if (e.key === 'Enter' || e.key === 'Tab') {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) return { handled: false }
      const res = accept(text)
      if (res) return { handled: true, ...res }
      // Nothing was inserted because the list is stale. Consume the key AND
      // arm it: dropping it is what makes the ordinary flow cost two Enters,
      // since at real latency every first Enter lands inside this window. With
      // no candidates at all (`handled: false`) the key falls through to the
      // textarea as usual.
      if (stale.value) {
        armed = token ? { query: query.value, start: token.start } : null
        return { handled: true }
      }
      return { handled: false }
    }
    return { handled: false }
  }

  return {
    open, query, activeIndex, filtered, loading, stale, armedResolved,
    ensureLoaded, sync, move, setActive, close, accept, handleKeydown,
  }
}
