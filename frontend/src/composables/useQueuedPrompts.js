import { ref, computed, reactive } from 'vue'
import api from '../api'

// Queued / steering prompts for the /live card. The server derives
// `queued_prompts` from the transcript (and tags a not-yet-flushed bridge
// steer `source:'bridge'`). A just-sent steer is held as an OPTIMISTIC entry
// until a poll returns it — via server queued_prompts or the real prompt span
// landing in the tail — or the TTL lapses. Never a client-stamped permanent
// row: the optimistic entry is dropped the moment the server represents it.
//
// A run regin owns also gives each entry a stable `id`, which is what makes a
// row editable and what the two mutations name. Both mutations are optimistic
// in the same spirit as the echo: the poll is up to 4s away, so a removed row
// hides at once and an edited one shows its new text at once, each held only
// until the server's own copy agrees (or stops existing).
const TTL_MS = 120000
// `agent_runs._PROMPT_MAX`. Mirrored rather than left to the server: the route
// stores `prompt[:8000]`, so an optimistic override holding the untruncated
// text would never match what comes back and `pruned()` would keep it forever
// — the card would show, for the rest of the session, a queued prompt that
// differs from the one the runner will actually send.
const PROMPT_MAX = 8000
const norm = (s) => (s || '').trim().replace(/\s+/g, ' ')

export function useQueuedPrompts(getQueued, getSpans, getSessionId = () => '') {
  const pendingSends = ref([])
  // id → the local view of a mutation the server hasn't confirmed yet:
  // `null` for a removal, a string for a rewrite.
  const localOps = ref(new Map())

  // The set of prompt texts the server already represents (queued or a landed
  // prompt span) — an optimistic entry is retired the moment one appears.
  function representedTexts() {
    const seen = new Set(
      (getQueued() || []).filter(q => q && q.content).map(q => norm(q.content)))
    for (const s of getSpans() || []) {
      if (s.name === 'prompt') seen.add(norm(s.attributes?.text))
    }
    return seen
  }

  function alivePending(seen = representedTexts(), now = Date.now()) {
    return pendingSends.value.filter(
      p => now - p.at < TTL_MS && !seen.has(norm(p.text)))
  }

  // noteSent is the only writer, so pruning to alive entries here bounds the
  // backing ref: expired/consumed rows never accumulate across a long session
  // (the computed alone filtered them from the view but left them in the ref).
  function noteSent(text) {
    const t = (text || '').trim()
    if (!t) return
    pendingSends.value = [
      ...alivePending().filter(p => p.text !== t), { text: t, at: Date.now() },
    ]
  }

  // Apply the pending mutations to the server's copy: a removal drops its row,
  // a rewrite overrides its text. Pure — settling happens in `pruned()`, not
  // here, because a computed that writes the ref it reads re-triggers itself.
  //
  // An op that has settled is already a no-op through this: the poll no longer
  // carries the removed id, and an edited row arrives with the text the
  // override would have written. So correctness never depends on pruning;
  // only the map's size does.
  function applyLocal(rows) {
    const ops = localOps.value
    if (!ops.size) return rows
    return rows.filter(q => ops.get(q.id) !== null)
      .map(q => (ops.has(q.id) ? { ...q, content: ops.get(q.id) } : q))
  }

  // Drop the ops the server has caught up with: a removal whose id is gone
  // from the queue, a rewrite whose text is now what the server serves. Run on
  // every write, which bounds the map without a watcher.
  function pruned() {
    const rows = (getQueued() || []).filter(q => q && q.id)
    const byId = new Map(rows.map(q => [q.id, q.content]))
    const next = new Map()
    for (const [id, pending] of localOps.value) {
      if (pending === null ? byId.has(id)
        : (byId.has(id) && norm(byId.get(id)) !== norm(pending))) {
        next.set(id, pending)
      }
    }
    return next
  }

  // Retire the optimistic echo `noteSent` left for the row `id` names.
  //
  // The echo is normally suppressed only *while* the server still represents
  // its text, and released back the moment it doesn't — which is right for a
  // prompt whose turn started (a `prompt` span lands and represents it
  // forever) and exactly wrong for one the operator just removed or rewrote:
  // no span is ever coming for a prompt that will not run, so the echo
  // resurfaces as a ghost chip carrying the old text, with no id and therefore
  // no way to remove it, for the rest of its TTL. Acting on a row is proof the
  // server represented it, so the echo has done its job either way.
  function retireEcho(id) {
    const row = (getQueued() || []).find(q => q && q.id === id)
    const key = norm(row?.content)
    if (!key) return
    pendingSends.value = pendingSends.value.filter(p => norm(p.text) !== key)
  }

  function noteRemoved(id) {
    if (!id) return
    retireEcho(id)
    localOps.value = pruned().set(id, null)
  }

  function noteEdited(id, text) {
    if (!id) return
    retireEcho(id)
    localOps.value = pruned().set(id, text)
  }

  // A refused mutation must not keep masking the row it failed on.
  function rollback(id) {
    if (!localOps.value.has(id)) return
    const next = pruned()
    next.delete(id)
    localOps.value = next
  }

  // ── Mutations (SDK-tier rows only — they are the ones carrying an id) ──
  // Ids with a request in flight; the row goes inert so a double-tap can't
  // fire a second edit or remove against the same entry.
  const busyIds = ref(new Set())
  // The last refusal, surfaced above the list. The interesting one is "that
  // prompt is already running": it means the poll that rendered the row is a
  // turn out of date, and the operator has to read that rather than watch the
  // row silently reappear.
  const notice = ref('')
  let noticeTimer = null

  function setBusy(id, on) {
    const next = new Set(busyIds.value)
    if (on) next.add(id)
    else next.delete(id)
    busyIds.value = next
  }

  function fail(id, detail) {
    rollback(id)
    notice.value = detail
    if (noticeTimer) clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => { notice.value = '' }, 6000)
  }

  async function mutate(id, request, optimistic, accepted) {
    if (!id || busyIds.value.has(id)) return false
    setBusy(id, true)
    notice.value = ''
    optimistic()
    let res = null
    try { res = await request() } catch { res = null }
    setBusy(id, false)
    if (accepted(res)) return true
    fail(id, res?.detail || res?.msg || 'the queue did not accept that')
    return false
  }

  function edit(id, rawText) {
    const text = (rawText || '').slice(0, PROMPT_MAX)
    return mutate(
      id,
      () => api.patch(`/agent-runs/${getSessionId()}/queue/${id}`,
                      { prompt: text }),
      () => noteEdited(id, text),
      res => !!res?.updated,
    )
  }

  function remove(id) {
    return mutate(
      id,
      () => api.del(`/agent-runs/${getSessionId()}/queue/${id}`),
      () => noteRemoved(id),
      res => !!res?.removed,
    )
  }

  const items = computed(() => {
    const server = applyLocal((getQueued() || [])
      .filter(q => q && q.content)
      .map(q => ({ id: q.id, content: q.content, source: q.source })))
    const optimistic = alivePending(representedTexts())
      .map(p => ({ content: p.text, optimistic: true }))
    return [...server, ...optimistic]
  })

  // Session switch: the persistent host view reuses this composable across
  // trace ids, so a leftover optimistic entry from the PREVIOUS session would
  // render under the new one (server queued_prompts is already scoped by
  // trace_id and needs no reset) — clear the client-only echo on every switch.
  // Queue ids are per-runner and would collide across sessions, so they go too.
  function reset() {
    pendingSends.value = []
    localOps.value = new Map()
    busyIds.value = new Set()
    notice.value = ''
    if (noticeTimer) { clearTimeout(noticeTimer); noticeTimer = null }
  }

  return reactive({ items, busyIds, notice, noteSent, edit, remove, reset })
}
