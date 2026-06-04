<script setup>
import Badge from '../Badge.vue'
import { fmtLocalDateTime } from '../../utils/traceFormatters'

defineProps({
  threads: { type: Array, default: () => [] },
})

function feedbackThreadColor(status) {
  if (status === 'addressed' || status === 'resolved') return 'green'
  if (status === 'dismissed') return 'gray'
  return 'yellow'
}
</script>

<template>
  <div v-if="threads.length" class="space-y-3">
    <h3 class="topics-subsection-title">General Review Notes</h3>
    <div
      v-for="thread in threads"
      :key="`general-${thread.id}`"
      class="rounded border border-amber-200 bg-amber-50/70 px-4 py-3"
    >
      <div class="flex flex-wrap items-center gap-2">
        <Badge :color="feedbackThreadColor(thread.resolution_state)" :label="thread.resolution_state || 'open'" />
        <span class="text-xs font-medium text-slate-700">Revision overview</span>
        <span v-if="thread.revision_number" class="text-[11px] text-slate-500">opened in r{{ thread.revision_number }}</span>
        <span v-if="thread.addressed_in_revision_number" class="text-[11px] text-slate-500">addressed in r{{ thread.addressed_in_revision_number }}</span>
      </div>
      <article
        v-for="comment in (thread.comments || [])"
        :key="`general-comment-${comment.id}`"
        class="mt-3 border-l-2 border-amber-300 pl-3"
      >
        <div class="flex items-center justify-between gap-2 text-[11px] text-slate-500">
          <span class="font-medium uppercase tracking-wide">{{ comment.author_kind }}</span>
          <span>{{ fmtLocalDateTime(comment.created_at) }}</span>
        </div>
        <p class="mt-1 whitespace-pre-wrap text-sm text-slate-800">{{ comment.body }}</p>
      </article>
    </div>
  </div>
</template>
