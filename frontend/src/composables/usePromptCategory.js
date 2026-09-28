import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

export const ALL = 'all'
const PARAM = 'category'

// Keyed on the registry's `PromptSurface.area` values (lib/prompts/). The card
// eyebrow and the filter chip both read this one map, so a row's badge and its
// chip can never disagree.
const AREAS = {
  defect: { label: 'Defect', color: 'red' },
  grader: { label: 'Grader', color: 'purple' },
  memory: { label: 'Memory', color: 'blue' },
  'task-run': { label: 'Task run', color: 'cyan' },
  'topic-proposal': { label: 'Topic proposal', color: 'green' },
  topics: { label: 'Topic graph', color: 'yellow' },
  other: { label: 'Other', color: 'gray' },
}

function titleCase(key) {
  const words = String(key || 'other').replace(/[-_]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function areaLabel(area) {
  return AREAS[area]?.label || titleCase(area)
}

export function areaColor(area) {
  return AREAS[area]?.color || 'gray'
}

export function rowArea(row) {
  return row.area || 'other'
}

// The category filter for one tab's rows, synced to `?category=<area>`. The
// value is derived from the route on every read (not seeded once), and an area
// with no rows in this tab reads as All — a stale or foreign link never hides
// the whole list.
export function usePromptCategory(rows) {
  const route = useRoute()
  const router = useRouter()

  const counts = computed(() => {
    const out = new Map()
    for (const r of rows.value) out.set(rowArea(r), (out.get(rowArea(r)) || 0) + 1)
    return out
  })

  const chips = computed(() =>
    [...counts.value.entries()]
      .map(([area, count]) => ({ area, count, label: areaLabel(area), color: areaColor(area) }))
      .sort((a, b) => a.label.localeCompare(b.label)),
  )

  const category = computed({
    get() {
      const want = route.query[PARAM]
      return want && counts.value.has(want) ? want : ALL
    },
    set(area) {
      const query = { ...route.query }
      if (!area || area === ALL) delete query[PARAM]
      else query[PARAM] = area
      router.push({ query })
    },
  })

  const filtered = computed(() =>
    category.value === ALL ? rows.value : rows.value.filter((r) => rowArea(r) === category.value),
  )

  // Replace, not push: correcting the filter so a deep-linked row is visible
  // is part of landing on that link, not a step Back should undo.
  async function reveal(row) {
    if (category.value === ALL || rowArea(row) === category.value) return
    await router.replace({ query: { ...route.query, [PARAM]: rowArea(row) } })
  }

  return {
    chips,
    category,
    filtered,
    reveal,
    total: computed(() => rows.value.length),
    showBar: computed(() => counts.value.size >= 2),
  }
}
