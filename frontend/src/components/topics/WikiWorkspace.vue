<script setup>
// The wiki tab's pinned two-pane Confluence layout: a persistent left BUCKET
// TREE (spaces → pages) beside the selected topic's wiki on the right. Mirrors
// the shell of components/memory/MemoryTaxonomy.vue (left rail + independently
// scrolling detail), but reads its `{roots, nodes}` tree from the workspace
// payload (`data.tree`) instead of a dedicated endpoint. Selecting a leaf sets
// `?topic=` (the existing chooseTopic contract) so a deep-link re-opens the
// same page; the tree stays visible in both the selected and unselected state.
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import WikiTree from './WikiTree.vue'
import ApprovedTopicDetail from './ApprovedTopicDetail.vue'

const props = defineProps({
  repo: { type: String, required: true },
  data: { type: Object, default: null },
})
defineEmits(['refresh-all', 'error'])

const route = useRoute()
const router = useRouter()

const filter = ref('')

const roots = computed(() => props.data?.tree?.roots || [])
const nodes = computed(() => props.data?.tree?.nodes || {})
const selectedId = computed(() => route.query.topic || null)

// Leaf topics are the pages; buckets are group headers. The header count
// mirrors ApprovedTopicsList/TaxonomyTree: filtered / total.
const leaves = computed(() => Object.values(nodes.value).filter((n) => !n.is_bucket))
const matchCount = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return leaves.value.length
  return leaves.value.filter((n) => `${n.label || ''} ${n.id || ''}`.toLowerCase().includes(q)).length
})
// Whether any node (bucket OR leaf) matches the active filter — the tree
// prunes to matches + ancestors, so no match means an empty tree pane.
const hasMatches = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return true
  return Object.values(nodes.value).some((n) => `${n.label || ''} ${n.id || ''}`.toLowerCase().includes(q))
})

function withQuery(next) {
  return { ...route.query, ...next }
}
function chooseTopic(id) {
  router.replace({ query: withQuery({ tab: 'wiki', topic: id || undefined }) })
}
</script>

<template>
  <!-- Below lg the fixed height + flex-col stack collapsed the detail to 0px
       (the aside consumed the whole container), so small viewports show ONE
       pane at a time keyed on ?topic= instead of the two-pane split. -->
  <div class="flex flex-col lg:flex-row lg:items-stretch border border-border rounded-lg lg:overflow-hidden bg-surface min-h-[28rem] h-auto lg:h-[calc(100vh-18rem)]">
    <!-- left rail: filter + bucket tree -->
    <aside
      class="w-full lg:w-72 lg:shrink-0 flex-col min-h-0 border-b lg:border-b-0 lg:border-r border-border bg-surface-2"
      :class="selectedId ? 'hidden lg:flex' : 'flex'"
    >
      <div class="p-2 border-b border-border-subtle space-y-2">
        <div class="flex items-baseline justify-between px-1 text-[11px] text-fg-faint font-mono tabular-nums">
          <span>{{ roots.length }} buckets</span>
          <span>{{ filter.trim() ? `${matchCount} / ${leaves.length}` : leaves.length }} pages</span>
        </div>
        <div class="relative">
          <Icon name="search" :size="14" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-faint pointer-events-none" />
          <Input v-model="filter" placeholder="Filter pages…" aria-label="Filter wiki pages" class="pl-8!" />
        </div>
      </div>
      <div class="flex-1 min-h-0 overflow-y-auto p-2">
        <p v-if="!roots.length" class="text-sm text-fg-faint px-1 py-2">
          No approved topics yet. Use Scan Topics or Generate Wiki above.
        </p>
        <p v-else-if="!hasMatches" class="text-sm text-fg-faint px-1 py-2">
          No pages match your filter.
        </p>
        <WikiTree
          v-else
          :roots="roots"
          :nodes="nodes"
          :selected-id="selectedId"
          :filter="filter"
          @select="chooseTopic"
        />
      </div>
    </aside>

    <!-- right pane: the selected topic's wiki, or a placeholder -->
    <div
      class="flex-1 min-w-0 min-h-0 lg:overflow-y-auto"
      :class="selectedId ? '' : 'hidden lg:block'"
    >
      <ApprovedTopicDetail
        v-if="selectedId"
        :repo="repo"
        :data="data"
        @refresh-all="$emit('refresh-all')"
        @error="(msg) => $emit('error', msg)"
      />
      <div v-else class="grid place-items-center h-full min-h-[20rem] p-6 text-center">
        <div class="space-y-2 text-fg-faint">
          <Icon name="tag" :size="26" class="mx-auto" />
          <p class="text-sm">Select a page from the tree to view its wiki.</p>
        </div>
      </div>
    </div>
  </div>
</template>
