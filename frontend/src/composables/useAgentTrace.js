import { ref, reactive, watch } from 'vue'
import api from '../api'

// The scoped agent's OWN spans, fetched from the child trace that serves
// them.
//
// A delegating parent's conversation carries only the agent's
// `subagent.start`/`subagent.stop` markers — its work is served under
// `sa-<agent_id>` as a full session. So every surface that used to
// re-project the parent's span list by `agent_id` (the desktop companion
// pane, the <xl takeover feed, the /live scoped tail) reads this instead:
// there is nothing left in the parent to partition.
//
// The full map, not `shallow=1`: a scope shows the agent's whole run at once
// and has no paging chrome of its own.
//
// The map is structure — its attribute bag is stripped to the map's keep-list
// — so each of the child's roots is then read deep, exactly as the main feed
// reads a prompt's subtree. Without that second read every scoped row would
// render as a bare tool name.
//
// `complete` rides back with the rows because the deep reads are allowed to
// fail individually: a fetch that lost one of them still returns a full row
// list, only with those rows stripped back to the map's keep-list. Stamping
// that as the loaded answer froze a pane at structure-only cards — bodies gone,
// and no later poll owing a re-read (see `loadedKey`).
async function fetchAgentSpans(agentId) {
  const base = `/sessions/sa-${encodeURIComponent(agentId)}`
  const data = await api.get(`${base}/map`)
  const byId = new Map()
  for (const s of data?.spans || []) byId.set(s.span_id, s)
  const roots = [...byId.values()].filter(
    (s) => !s.parent_id || !byId.has(s.parent_id))
  const deep = await Promise.all(roots.flatMap((r) => {
    const id = encodeURIComponent(r.span_id)
    return [
      api.get(`${base}/spans/${id}/children?deep=1`).catch(() => null),
      // A root is not its own descendant, so it is the one span the deep read
      // leaves stripped — read its body directly. Merged UNDER the map's bag:
      // serve-time fields (the agent rollups) are stamped on the projection
      // only, and /content would otherwise erase them.
      api.get(`${base}/spans/${id}/content`)
        .then((c) => ({ spans: [{ ...r, attributes: { ...(r.attributes || {}), ...(c?.attributes || {}) } }] }))
        .catch(() => null),
    ]
  }))
  // ENRICH ONLY. The map is the one answer that ran the serve-time merge, so
  // it alone decides which ids this fetch serves; the deep reads are taken
  // later and only say what is IN those rows. Letting them insert too made a
  // call that resolved between the two reads render twice — the map's
  // still-PENDING placeholder as a bare running chip, beside the resolved twin
  // the deep read introduced. A row that landed after the map snapshot is not
  // lost, only deferred: the roster facts that produced it also key the next
  // refresh.
  for (const d of deep) {
    for (const s of d?.spans || []) {
      if (byId.has(s.span_id)) byId.set(s.span_id, s)
    }
  }
  const spans = [...byId.values()].map(
    (s) => (s.attributes ? s : { ...s, attributes: {} }))
  // An empty answer is never complete either. `session_spans` is append-only,
  // so a child trace that has served rows cannot shrink back to none: a 0-span
  // map is an id that did not resolve to an agent, which is an ordinary race
  // while its first rows land — and reading it as the truth blanked a pane that
  // was already rendering the whole run.
  return { complete: deep.every((d) => d !== null) && spans.length > 0, spans }
}

export function useAgentTrace(getAgentId) {
  const spans = ref([])
  const loading = ref(false)
  // A fast A→B scope switch must not let A's in-flight response land under
  // B's identity, and a poll refresh must not resurrect a scope already left.
  let generation = 0
  // The roster facts the loaded spans answer for (`refresh`'s key). Keyed on
  // the agent's own identifiers — span count, newest row, status — so the
  // child trace is re-read exactly when it can have changed, including once
  // more when the agent finishes: the rows that land between the last poll
  // and the stop marker are not visible to a liveness-gated refresh.
  //
  // Null means "nothing on screen answers for the current facts", which is the
  // only thing that makes a retry happen: all three of those facts FREEZE once
  // the agent finishes, so a refresh that stamped the key after a failed or
  // partial fetch was the last one this agent would ever get.
  let loadedKey = null

  async function load(agentId, { quiet = false, key = null } = {}) {
    const gen = ++generation
    if (!agentId) {
      spans.value = []
      loading.value = false
      loadedKey = null
      return
    }
    if (!quiet) {
      spans.value = []
      loading.value = true
    }
    try {
      const next = await fetchAgentSpans(agentId)
      if (gen !== generation) return
      // A partial answer never REPLACES a whole one: its rows are the same ids
      // with thinner bodies, so overwriting would blank the cards the reader is
      // looking at. Rendering it is still right when there is nothing to lose.
      if (next.complete || !spans.value.length) spans.value = next.spans
      loadedKey = next.complete ? key : null
    } catch (e) {
      if (gen !== generation) return
      // The rows on screen stay: they are the last thing the server actually
      // said about this agent. A scope that empties itself because one poll
      // could not reach the server (a restart, a slept tab) reads as the
      // agent's spans having been lost, and nothing ever brought them back.
      loadedKey = null
    } finally {
      if (gen === generation) loading.value = false
    }
  }

  // Live poll: re-read in place when the agent's roster facts moved. No
  // clear, no spinner — the scope is already on screen and only grows.
  function refresh(agent) {
    const id = getAgentId()
    if (!id || !agent) return Promise.resolve()
    const key = `${agent.status}|${agent.spanCount}|${agent.lastSeen}`
    if (key === loadedKey) return Promise.resolve()
    return load(id, { quiet: true, key })
  }

  watch(getAgentId, (id) => load(id), { immediate: true })

  return reactive({ spans, loading, refresh })
}
