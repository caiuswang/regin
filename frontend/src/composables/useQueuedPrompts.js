import { computed, reactive, ref } from 'vue'
import api from '../api'

// Queued / steering prompts for the /live card — a pure render of the
// server's `queued_prompts`. Every row is server-authoritative from the
// moment a send is delivered: an SDK-tier steer is the runner's in-memory
// queue entry, a bridge-tier steer is its pending `bridge_messages` row, and
// each leaves the feed only on an observed event (turn started, transcript
// consumed it, session ended, operator removed it). There is no client-side
// optimistic echo and no TTL — the old echo expired on a timer and could
// resurface as a ghost chip for a body the transcript never logs verbatim
// (an executed slash command, say). The send's only client-visible trace is
// the composer's "delivered" flash until the next poll serves the row.
//
// A row's `id` is what makes it mutable and what the mutations name: the SDK
// tier's queue ids (regin's own list, editable + removable) and the bridge
// tier's `b<row>` chip ids (dismiss only — the keystrokes were already
// typed). Both mutations are optimistic: the poll is up to 4s away, so a
// removed row hides at once and an edited one shows its new text at once,
// each held only until the server's own copy agrees (or stops existing).

// `agent_runs._PROMPT_MAX`. Mirrored rather than left to the server: the route
// stores `prompt[:8000]`, so an optimistic override holding the untruncated
// text would never match what comes back and `pruned()` would keep it forever
// — the card would show, for the rest of the session, a queued prompt that
// differs from the one the runner will actually send.
const PROMPT_MAX = 8000
const norm = (s) => (s || '').trim().replace(/\s+/g, ' ')

export function useQueuedPrompts(getQueued, getSessionId = () => '') {
  // id → the local view of a mutation the server hasn't confirmed yet:
  // `null` for a removal, a string for a rewrite.
  const localOps = ref(new Map())

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

  function noteRemoved(id) {
    if (!id) return
    localOps.value = pruned().set(id, null)
  }

  function noteEdited(id, text) {
    if (!id) return
    localOps.value = pruned().set(id, text)
  }

  // A refused mutation must not keep masking the row it failed on.
  function rollback(id) {
    if (!localOps.value.has(id)) return
    const next = pruned()
    next.delete(id)
    localOps.value = next
  }

  // ── Mutations (rows carrying an id) ──
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

  const items = computed(() => applyLocal((getQueued() || [])
    .filter(q => q && q.content)
    .map(q => ({ id: q.id, content: q.content, source: q.source }))))

  // Session switch: the persistent host view reuses this composable across
  // trace ids. Queue ids are per-runner (and bridge row ids per-session
  // meaningless elsewhere), so a leftover op would collide across sessions.
  function reset() {
    localOps.value = new Map()
    busyIds.value = new Set()
    notice.value = ''
    if (noticeTimer) { clearTimeout(noticeTimer); noticeTimer = null }
  }

  return reactive({ items, busyIds, notice, edit, remove, reset })
}
