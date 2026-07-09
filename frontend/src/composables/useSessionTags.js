import { ref } from 'vue'
import api from '../api'

// Custom-tag state + mutations for the Sessions list. Builtin category tags
// (user / topic-proposal / system) are derived server-side and ride each
// row's `tags`; this composable owns only the *custom* side: the facet's
// custom-tag options and the per-row add/remove calls. Kept out of
// SessionsView so that view stays under the vue-complexity thresholds.
export function useSessionTags() {
  // [{ slug, count }] — every custom tag in use, for the facet + autocomplete.
  const customTags = ref([])

  async function loadCustomTags() {
    try {
      const res = await api.get('/session-tags')
      customTags.value = res.tags || []
    } catch {
      customTags.value = []
    }
  }

  // Rebuild a row's flat `tags` list from its (unchanging) builtin category
  // plus the custom tags the server just returned — so the row reflects an
  // add/remove without a full list refetch. Each custom entry is
  // `{ slug, source }` ('manual' | 'auto'); `source` is preserved so a manual
  // mutation never relabels the row's auto tags as manual.
  function buildRowTags(category, customTags) {
    const tags = [{ slug: category, source: 'auto', builtin: true }]
    for (const t of customTags) {
      tags.push({ slug: t.slug, source: t.source || 'manual', builtin: false })
    }
    return tags
  }

  // Patch one row's tags after a mutation. `itemsRef` is useCursor's
  // shallowRef, so an in-place `row.tags =` is NOT tracked — reassign the
  // array with a fresh row object so desktop + mobile chips re-render.
  function patchRowTags(itemsRef, traceId, customSlugs) {
    itemsRef.value = itemsRef.value.map(s =>
      s.trace_id === traceId
        ? { ...s, tags: buildRowTags(s.category, customSlugs) }
        : s)
  }

  // Returns the new custom-slug list on success, or null on failure so the
  // caller can leave the row untouched.
  async function addTag(traceId, slug) {
    const res = await api.post(`/sessions/${traceId}/tags`, { tag: slug })
    return res && res.ok ? res.tags : null
  }

  async function removeTag(traceId, slug) {
    const res = await api.del(`/sessions/${traceId}/tags/${encodeURIComponent(slug)}`)
    return res && res.ok ? res.tags : null
  }

  return { customTags, loadCustomTags, patchRowTags, addTag, removeTag }
}
