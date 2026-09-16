import { ref, computed } from 'vue'
import api from '../api'
import { isSyntheticSpanId } from './useSpanTree.js'

// On-demand span content (attributes) cache for the trace view. The shallow
// /map load ships root spans without their full attribute bag; the detail
// panel and conversation cards fetch it lazily by span_id and cache it here.
//
// `allSpans` overlays the cache onto `session.spans` so every consumer reads a
// single merged list. The lazily-fetched `/content` attributes win on shared
// keys (they carry the full, untruncated bag), but we MERGE rather than replace
// so serve-time-derived fields survive: `reclaimed_tokens` (compaction) and
// `main_session_impact_tokens` (subagent) are stamped onto the shallow /map
// projection only — the per-span /content endpoint never recomputes them, so a
// blind replace would erase the chip the moment the span's content is fetched
// (e.g. on selection). `session` is the SFC-owned ref; `route` supplies the id.
export function useSpanContentCache(session, route) {
  const spanContentCache = ref(new Map())

  // Recomputed on every poll — `session.value` is reassigned whenever the tail
  // reconciles, and `server_now` alone moves each tick — so it must not clone
  // what it isn't changing. The overlay applies to the handful of spans the
  // user has actually opened; cloning all of them (and their attribute bags)
  // allocated two objects per span per tick, which on a session carrying a
  // deep-loaded subtree is tens of MB of garbage a minute.
  //
  // Skipping the clone means `attributes` is no longer normalised HERE, and
  // templates read `span.attributes.x` unguarded — so every writer into
  // `session.spans` guarantees the object instead (`normalizeSpans` /
  // `mergeLoadedSpans` in useTraceData).
  // Any span list, not just the session's: a scoped agent's spans come from
  // ITS trace (useAgentTrace) while their bodies are still fetched — and
  // cached — against the parent they are stored under, so both lists have to
  // read through the one cache or the scoped feed renders empty rows.
  function withContent(spans) {
    const cache = spanContentCache.value
    if (!cache.size) return spans
    return spans.map(s => {
      const cached = cache.get(s.span_id)
      if (!cached) return s
      return { ...s, attributes: { ...(s.attributes || {}), ...cached } }
    })
  }

  const allSpans = computed(() =>
    (session.value ? withContent(session.value.spans || []) : []))

  // `force` re-reads a span whose body is still being WRITTEN. The cache is
  // otherwise write-once, and because its entry is overlaid ON TOP of every
  // later /map projection, a span cached mid-stream is frozen at whatever
  // length it had when it was first opened — the poll keeps delivering the
  // longer body and the overlay keeps hiding it. Callers reading a live span
  // (the presentation reader) pass force so growth actually reaches the page.
  async function fetchSpanContent(spanId, { force = false } = {}) {
    // Synthesized scoped-task prompt ids exist only client-side — 404 upstream.
    if (isSyntheticSpanId(spanId)) return {}
    if (!force && spanContentCache.value.has(spanId)) return spanContentCache.value.get(spanId)
    try {
      const data = await api.get(`/sessions/${route.params.id}/spans/${spanId}/content`)
      const attrs = data.attributes || {}
      // Precautionary, NOT a fixed bug: `force` is the only way this cache can
      // ever overwrite a populated entry, so it must not do so with nothing. A
      // 200 carrying no attributes therefore leaves an existing body alone.
      // (An attempt to exercise this through the UI could not reach it — the
      // reader still rendered from the /map projection — so this is reasoned
      // defence over the new write path, not a reproduced failure.)
      if (Object.keys(attrs).length || !spanContentCache.value.has(spanId)) {
        spanContentCache.value.set(spanId, attrs)
      }
      return spanContentCache.value.get(spanId) ?? attrs
    } catch (e) {
      console.error('Failed to fetch span content:', e)
      return {}
    }
  }

  return { spanContentCache, allSpans, withContent, fetchSpanContent }
}
