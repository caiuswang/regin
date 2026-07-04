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

const cache = new Map() // sessionId -> command rows

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

  const filtered = computed(() => {
    if (!open.value) return []
    const q = query.value.toLowerCase()
    if (!q) return catalog.value
    const starts = []
    const contains = []
    for (const c of catalog.value) {
      const name = c.name.toLowerCase()
      if (name.startsWith(q)) starts.push(c)
      else if (name.includes(q) || (c.description || '').toLowerCase().includes(q)) contains.push(c)
    }
    return [...starts, ...contains]
  })

  async function ensureLoaded(sessionId) {
    if (!sessionId) return
    if (cache.has(sessionId)) { catalog.value = cache.get(sessionId); return }
    try {
      const res = await api.get(`/sessions/${sessionId}/bridge-commands`)
      const rows = (res && res.commands) || []
      cache.set(sessionId, rows)
      catalog.value = rows
    } catch { catalog.value = [] }
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

  // Replace the leading `/token` with `/<name> `. Returns { text, caret } for
  // the caller to apply, or null when nothing is selectable.
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
    open, query, activeIndex, filtered,
    ensureLoaded, sync, move, setActive, close, accept, handleKeydown,
  }
}
