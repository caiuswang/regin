<script setup>
// One STATS cell per feature row: tasks done, rounds · last activity, and the
// defect tally when any defect is blamed. Same fields and links as classic's
// four cells, so the testids carry over.
import { computed } from 'vue'
import { ago, defectTally } from '../../../../utils/pipelineFormat'
import { defectsLink, tasksLink } from '../../../../utils/featureLinks'

const props = defineProps({
  row: { type: Object, required: true },
})

const rounds = computed(() => {
  const n = Math.max(0, props.row.rounds || 0)
  return `${n} round${n === 1 ? '' : 's'}`
})
</script>

<template>
  <span role="cell" data-pl-part="stats">
    <span data-pl-part="stat-line">
      <span data-pl-part="stat-mark" aria-hidden="true">◎</span>
      <router-link
        v-if="row.tasks"
        :to="tasksLink(row.id)"
        data-pl-part="stat-link"
        :data-testid="`feature-tasks-link-${row.id}`"
      >{{ row.tasks_done }}/{{ row.tasks }} tasks</router-link>
      <span v-else>0/0 tasks</span>
    </span>
    <span data-pl-part="stat-line" data-testid="feature-rounds" :aria-label="`${row.rounds} rounds`">
      <span data-pl-part="stat-mark" aria-hidden="true">↻</span><span data-pl-part="stat-text"><span>{{ rounds }} ·</span> <span>{{ ago(row.last_activity) }}</span></span>
    </span>
    <span v-if="row.defects.total" data-pl-part="stat-line" :data-warn="row.defects.open ? '' : undefined">
      <span data-pl-part="stat-mark" aria-hidden="true">✱</span>
      <router-link
        :to="defectsLink(row.id)"
        data-pl-part="stat-link"
        :data-testid="`feature-defects-link-${row.id}`"
      >{{ defectTally(row.defects) }}</router-link>
    </span>
  </span>
</template>
