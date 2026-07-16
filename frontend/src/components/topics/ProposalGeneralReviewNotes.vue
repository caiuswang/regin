<script setup>
import { computed } from 'vue'
import ReviewOverviewCard from './ReviewOverviewCard.vue'

const props = defineProps({
  threads: { type: Array, default: () => [] },
})

// Show only the most-recent review note expanded; the rest fold behind a
// <details>. The threads arrive newest-first, but sort defensively so the
// head is genuinely the latest regardless of caller order.
const orderedThreads = computed(() =>
  [...props.threads].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || ''))),
)
const headThread = computed(() => orderedThreads.value[0] || null)
const restThreads = computed(() => orderedThreads.value.slice(1))
</script>

<template>
  <div v-if="orderedThreads.length" class="space-y-3">
    <h3 class="topics-subsection-title">General Review Notes</h3>
    <ReviewOverviewCard v-if="headThread" :thread="headThread" />
    <details v-if="restThreads.length">
      <summary class="cursor-pointer text-xs font-medium text-slate-500">
        Show {{ restThreads.length }} more review note{{ restThreads.length === 1 ? '' : 's' }}
      </summary>
      <div class="mt-2 space-y-3">
        <ReviewOverviewCard
          v-for="thread in restThreads"
          :key="`general-${thread.id}`"
          :thread="thread"
        />
      </div>
    </details>
  </div>
</template>
