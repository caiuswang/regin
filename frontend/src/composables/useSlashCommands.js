// Slash-command / skill autocomplete for the /live bridge composer.
//
// Owns ALL popup state (catalog fetch+cache, query parsing, filtering,
// highlight nav, accept) so LiveComposer.vue stays a thin wiring layer under
// the vue-complexity gate. The menu opens only when the draft's first
// non-whitespace char is `/` and the caret sits inside that first token —
// matching Claude Code's slash semantics (commands fire at message start),
// so a `/` mid-sentence or inside a URL never triggers it.
//
// Catalog comes from GET /api/sessions/<id>/bridge-commands — the slash
// commands + skills the *target session* accepts, enumerated server-side from
// its own `.claude/` + `~/.claude/`. Cached per session (module-level) so
// re-focusing or switching back doesn't refetch.
import { ref, computed } from 'vue'
import api from '../api'

const cache = new Map() // sessionId -> { rows, at }
// sessionId -> in-flight fetch. The cache only fills after the await, so
// without this every keystroke during a cold (multi-second) catalog handshake
// would start its own request — and the composer calls in on both `input` and
// `keyup`, i.e. twice per key.
const inflight = new Map()
// An empty catalog is not a real answer: it is what the endpoint returns while
// its own SDK-failure cache is warm (30s server-side). Caching it like a real
// one pins the menu empty until a page reload, so it expires — fast enough to
// recover inside that window, and cheap because the retries land on the
// server's negative cache rather than a fresh handshake.
const EMPTY_TTL_MS = 5_000

// Every string a row answers to: its canonical name plus the aliases the raw
// terminal accepts for it (`/reset` → `clear`, `/proactive` → `loop`). The
// endpoint always sends `aliases` — `[]` on its fallback scan path — but a row
// cached from an older response can omit it entirely.
function namesOf(c) {
  const aliases = Array.isArray(c.aliases) ? c.aliases : []
  return [c.name, ...aliases]
    .filter((n) => typeof n === 'string')
    .map((n) => n.toLowerCase())
}

// 0 = prefix hit (best), 1 = substring hit, -1 = no match. An alias scores
// exactly like the name it stands for, so `/reset` ranks `clear` above a row
// that only mentions "reset" in its description.
function rank(c, q) {
  const names = namesOf(c)
  if (names.some((n) => n.startsWith(q))) return 0
  if (names.some((n) => n.includes(q))) return 1
  return (c.description || '').toLowerCase().includes(q) ? 1 : -1
}

// The leading `/token` of a draft: whitespace prefix, then `/`, then the
// run of non-whitespace chars. Returns { offset, name } or null.
function leadToken(text) {
  const lead = text.replace(/^\s*/, '')
  if (!lead.startsWith('/')) return null
  const m = lead.match(/^\/(\S*)/)
  return { offset: text.length - lead.length, name: m ? m[1] : '' }
}

export function useSlashCommands() {
  const catalog = ref([])
  const open = ref(false)
  const query = ref('')
  const activeIndex = ref(0)
  // The SDK-backed handshake behind the catalog can take seconds on a cold
  // session; without this the menu would claim "no command matches" meanwhile.
  const loading = ref(false)
  // Constant: the catalog is filtered client-side, so its rows always answer
  // the current query. Exported only to keep both menus' surface identical.
  const stale = ref(false)

  const filtered = computed(() => {
    if (!open.value) return []
    const q = query.value.toLowerCase()
    if (!q) return catalog.value
    const starts = []
    const contains = []
    for (const c of catalog.value) {
      const r = rank(c, q)
      if (r === 0) starts.push(c)
      else if (r === 1) contains.push(c)
    }
    return [...starts, ...contains]
  })

  function fetchCatalog(sessionId) {
    const pending = inflight.get(sessionId)
    if (pending) return pending
    const p = api.get(`/sessions/${sessionId}/bridge-commands`)
      .then((res) => {
        const rows = (res && res.commands) || []
        cache.set(sessionId, { rows, at: Date.now() })
        return rows
      })
      .catch(() => [])
      .finally(() => inflight.delete(sessionId))
    inflight.set(sessionId, p)
    return p
  }

  function cached(sessionId) {
    const hit = cache.get(sessionId)
    if (!hit) return null
    if (hit.rows.length === 0 && Date.now() - hit.at > EMPTY_TTL_MS) return null
    return hit.rows
  }

  async function ensureLoaded(sessionId) {
    if (!sessionId) return
    const hit = cached(sessionId)
    if (hit) { catalog.value = hit; return }
    loading.value = true
    catalog.value = await fetchCatalog(sessionId)
    loading.value = false
  }

  function clampIndex() {
    const n = filtered.value.length
    if (activeIndex.value > n - 1) activeIndex.value = Math.max(0, n - 1)
    if (activeIndex.value < 0) activeIndex.value = 0
  }

  // Recompute open/query from the draft + caret. Closes when the caret leaves
  // the leading `/token` (or there is no such token).
  function sync(text, caret) {
    const tok = leadToken(text)
    if (!tok || caret < tok.offset || caret > tok.offset + 1 + tok.name.length) {
      close()
      return
    }
    query.value = tok.name
    open.value = true
    clampIndex()
  }

  function move(delta) {
    const n = filtered.value.length
    if (n === 0) return
    activeIndex.value = (activeIndex.value + delta + n) % n
  }

  function setActive(i) { activeIndex.value = i }

  function close() {
    open.value = false
    query.value = ''
    activeIndex.value = 0
  }

  // Replace the leading `/token` with `/<name> ` — always the canonical name,
  // so a query that matched an alias completes to the real command. Returns
  // { text, caret } for the caller to apply, or null when nothing is selectable.
  function accept(text, item) {
    const chosen = item || filtered.value[activeIndex.value]
    const tok = leadToken(text)
    if (!chosen || !tok) return null
    const before = text.slice(0, tok.offset)
    const after = text.slice(tok.offset + 1 + tok.name.length)
    const insert = `/${chosen.name} `
    close()
    return { text: before + insert + after, caret: (before + insert).length }
  }

  // Consume nav/accept/dismiss keys while the menu is open. Returns
  // { handled, text?, caret? } — text present means the caller should apply an
  // accepted draft. Cmd/Ctrl+Enter is left unhandled so the composer can send.
  function handleKeydown(e, text) {
    if (!open.value) return { handled: false }
    if (e.key === 'ArrowDown') { move(1); return { handled: true } }
    if (e.key === 'ArrowUp') { move(-1); return { handled: true } }
    if (e.key === 'Escape') { close(); return { handled: true } }
    if (e.key === 'Enter' || e.key === 'Tab') {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) return { handled: false }
      const res = accept(text)
      return res ? { handled: true, ...res } : { handled: false }
    }
    return { handled: false }
  }

  return {
    open, query, activeIndex, filtered, loading, stale,
    ensureLoaded, sync, move, setActive, close, accept, handleKeydown,
  }
}
