<script setup>
import { computed } from 'vue'
import Badge from '../Badge.vue'
import ClampedText from '../ui/ClampedText.vue'
import { fmtLocalDateTime } from '../../utils/traceFormatters'

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

function feedbackThreadColor(status) {
  if (status === 'addressed' || status === 'resolved') return 'green'
  if (status === 'dismissed') return 'gray'
  return 'yellow'
}
</script>

<template>
  <div v-if="orderedThreads.length" class="space-y-3">
    <h3 class="topics-subsection-title">General Review Notes</h3>
    <template v-for="thread in [headThread]" :key="`general-${thread.id}`">
      <div class="rounded border border-amber-200 bg-amber-50/70 px-4 py-3">
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
          <ClampedText :lines="6" class="mt-1">
            <p class="whitespace-pre-wrap break-words text-sm text-slate-800">{{ comment.body }}</p>
          </ClampedText>
        </article>
      </div>
    </template>
    <details v-if="restThreads.length">
      <summary class="cursor-pointer text-xs font-medium text-slate-500">
        Show {{ restThreads.length }} more review note{{ restThreads.length === 1 ? '' : 's' }}
      </summary>
      <div class="mt-2 space-y-3">
        <div
          v-for="thread in restThreads"
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
            <ClampedText :lines="6" class="mt-1">
            <p class="whitespace-pre-wrap break-words text-sm text-slate-800">{{ comment.body }}</p>
          </ClampedText>
          </article>
        </div>
      </div>
    </details>
  </div>
</template>
