import { ref, computed, reactive } from 'vue'

// Queued / steering prompts for the /live card. The server derives
// `queued_prompts` from the transcript (and tags a not-yet-flushed bridge
// steer `source:'bridge'`). A just-sent steer is held as an OPTIMISTIC entry
// until a poll returns it — via server queued_prompts or the real prompt span
// landing in the tail — or the TTL lapses. Never a client-stamped permanent
// row: the optimistic entry is dropped the moment the server represents it.
const TTL_MS = 120000
const norm = (s) => (s || '').trim().replace(/\s+/g, ' ')

export function useQueuedPrompts(getQueued, getSpans) {
  const pendingSends = ref([])

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

  const items = computed(() => {
    const server = (getQueued() || [])
      .filter(q => q && q.content)
      .map(q => ({ content: q.content, source: q.source }))
    const optimistic = alivePending(representedTexts())
      .map(p => ({ content: p.text, optimistic: true }))
    return [...server, ...optimistic]
  })

  // Session switch: the persistent host view reuses this composable across
  // trace ids, so a leftover optimistic entry from the PREVIOUS session would
  // render under the new one (server queued_prompts is already scoped by
  // trace_id and needs no reset) — clear the client-only echo on every switch.
  function reset() {
    pendingSends.value = []
  }

  return reactive({ items, noteSent, reset })
}
