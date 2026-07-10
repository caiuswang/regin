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

  const allSpans = computed(() => {
    if (!session.value) return []
    const spans = session.value.spans || []
    return spans.map(s => {
      const cached = spanContentCache.value.get(s.span_id)
      return {
        ...s,
        attributes: cached ? { ...(s.attributes || {}), ...cached } : (s.attributes || {}),
      }
    })
  })

  async function fetchSpanContent(spanId) {
    // Synthesized scoped-task prompt ids exist only client-side — 404 upstream.
    if (isSyntheticSpanId(spanId)) return {}
    if (spanContentCache.value.has(spanId)) return spanContentCache.value.get(spanId)
    try {
      const data = await api.get(`/sessions/${route.params.id}/spans/${spanId}/content`)
      const attrs = data.attributes || {}
      spanContentCache.value.set(spanId, attrs)
      return attrs
    } catch (e) {
      console.error('Failed to fetch span content:', e)
      return {}
    }
  }

  return { spanContentCache, allSpans, fetchSpanContent }
}
