import { ref, computed } from 'vue'
import api from '../api'

// On-demand span content (attributes) cache for the trace view. The shallow
// /map load ships root spans without their full attribute bag; the detail
// panel and conversation cards fetch it lazily by span_id and cache it here.
//
// `allSpans` overlays the cache onto `session.spans` so every consumer reads a
// single merged list (cached attributes win over the shallow placeholder).
// `session` is the shared ref owned by the SFC; `route` supplies the id.
export function useSpanContentCache(session, route) {
  const spanContentCache = ref(new Map())

  const allSpans = computed(() => {
    if (!session.value) return []
    const spans = session.value.spans || []
    return spans.map(s => {
      const cached = spanContentCache.value.get(s.span_id)
      return { ...s, attributes: cached || s.attributes || {} }
    })
  })

  async function fetchSpanContent(spanId) {
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
