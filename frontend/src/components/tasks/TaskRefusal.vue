<script setup>
// A refusal the row keeps showing. The 409 from `run` names the dependencies
// still blocking the task, and each is rendered as a link to that task rather
// than a bare number — an operator told only "blocked by 7" has to go find 7.
// A refusal for capacity carries the slot holders instead, so the operator can
// free one where they stand; `release` is the owner's, since the refusal map
// is not this component's to edit.
import api from '../../api'
import RunSlotList from './RunSlotList.vue'

const props = defineProps({
  refusal: { type: Object, required: true },
})
const emit = defineEmits(['focus', 'released'])
const canWrite = api.getStoredUser()?.role !== 'viewer'

function released(traceId) {
  props.refusal.release?.(traceId)
  emit('released', traceId)
}
</script>

<template>
  <div v-if="refusal.slots" class="task-refusal" role="alert" data-pl-part="refusal" data-testid="task-refusal">
    <span data-pl-part="refusal-head" data-testid="task-refusal-slots">Run slots full ({{ refusal.slots.used }}/{{ refusal.slots.limit }})</span>
    <RunSlotList
      :holders="refusal.slots.holders" :can-write="canWrite"
      testid="task-refusal-holders"
      @closed="released"
    />
  </div>
  <p v-else class="task-refusal" role="alert" data-pl-part="refusal" data-testid="task-refusal">
    {{ refusal.message }}
    <template v-if="refusal.blocking.length">
      <span class="task-refusal-links">
        <RouterLink
          v-for="id in refusal.blocking"
          :key="id"
          class="task-refusal-link"
          :to="`/tasks#task-${id}`"
          :data-testid="`task-blocker-${id}`"
          @click="$emit('focus', id)"
        >task {{ id }}</RouterLink>
      </span>
    </template>
  </p>
</template>

<style scoped>
.task-refusal {
    margin-top: 0.5rem;
    font-size: 0.75rem;
    line-height: 1.5;
    color: var(--color-danger);
    overflow-wrap: anywhere;
}
.task-refusal-links {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 0.375rem;
    margin-left: 0.25rem;
}
.task-refusal-link {
    color: var(--color-danger-strong);
    text-decoration: underline;
    font-variant-numeric: tabular-nums;
}
</style>
