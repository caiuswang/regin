<script setup>
// The feature's task graph above its spec: the same component and the same
// endpoint as the Tasks tab's Graph view, drawn read-only. Running and editing
// edges stay on the full graph — this card only shows the shape and links out.
//
// Until the graph answers it draws nothing, and a feature with no tasks never
// gets the anchor, so the section index has nothing to point at.
import { computed, ref, watch } from 'vue'
import TaskLaneGraph from '../tasks/TaskLaneGraph.vue'
import { GRAPH_ANCHOR, tasksGraphLink } from '../../utils/featureLinks'

const props = defineProps({
  featureId: { type: Number, required: true },
})
const emit = defineEmits(['counted'])

const body = ref(null)
const params = computed(() => ({ feature_id: String(props.featureId) }))
const total = computed(() => body.value?.total_nodes ?? 0)
const summary = computed(() => {
  const b = body.value
  if (!b) return ''
  if (b.truncated) return `showing ${b.nodes.length} of ${b.total_nodes}`
  return `${b.total_nodes} ${b.total_nodes === 1 ? 'task' : 'tasks'}`
})

watch(() => props.featureId, () => { body.value = null; emit('counted', 0) })

function onLoaded(next) {
  body.value = next
  emit('counted', next.total_nodes)
}

const graph = ref(null)
defineExpose({ reload: () => graph.value?.reload() })
</script>

<template>
  <section
    v-show="total > 0"
    :id="total > 0 ? GRAPH_ANCHOR : undefined"
    class="fgp"
    data-testid="feature-graph-preview"
  >
    <header class="fgp-head">
      <h2 class="fgp-title">Task graph</h2>
      <span class="fgp-count" data-testid="feature-graph-count">{{ summary }}</span>
      <router-link class="fgp-open" :to="tasksGraphLink(featureId)" data-testid="feature-graph-open">
        Open full graph
      </router-link>
    </header>
    <TaskLaneGraph
      ref="graph"
      :params="params"
      :open="false"
      readonly
      compact
      @loaded="onLoaded"
    />
  </section>
</template>

<style scoped>
.fgp {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-xl);
    background: var(--color-surface);
    padding: 1rem 1.1rem;
}
.fgp-head {
    display: flex;
    align-items: baseline;
    gap: 0.625rem;
    margin-bottom: 0.625rem;
}
.fgp-title {
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--color-fg);
}
.fgp-count {
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
    font-variant-numeric: tabular-nums;
}
.fgp-open {
    margin-left: auto;
    font-size: 0.8125rem;
    font-weight: 500;
    color: var(--color-primary);
}
.fgp-open:hover { text-decoration: underline; }
.fgp-open:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
</style>
