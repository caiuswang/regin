<script setup>
import { computed } from 'vue'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import ClampedText from '../ui/ClampedText.vue'
import MarkdownContent from '../MarkdownContent.vue'
import { useCopy } from '../../composables/useCopy.js'
import { fmtLocalDateTime } from '../../utils/traceFormatters'

const props = defineProps({
  thread: { type: Object, required: true },
})

const { copyText } = useCopy()

// The reviewer writes the note as free-form markdown; the copy affordance hands
// back that same source text (all comments joined), not the rendered HTML.
const copyBody = computed(() =>
  (props.thread.comments || []).map((c) => c.body).filter(Boolean).join('\n\n'))

function feedbackThreadColor(status) {
  if (status === 'addressed' || status === 'resolved') return 'green'
  if (status === 'dismissed') return 'gray'
  return 'yellow'
}
</script>

<template>
  <div class="rounded border border-amber-200 bg-amber-50/70 px-4 py-3" data-testid="review-overview-card">
    <div class="flex flex-wrap items-center gap-2">
      <Badge :color="feedbackThreadColor(thread.resolution_state)" :label="thread.resolution_state || 'open'" />
      <span class="text-xs font-medium text-slate-700">Revision overview</span>
      <span v-if="thread.revision_number" class="text-[11px] text-slate-500">opened in r{{ thread.revision_number }}</span>
      <span v-if="thread.addressed_in_revision_number" class="text-[11px] text-slate-500">addressed in r{{ thread.addressed_in_revision_number }}</span>
      <Button
        v-if="copyBody"
        variant="ghost"
        size="sm"
        class="ml-auto gap-1 px-1 py-0.5 text-[11px] text-slate-500 hover:text-slate-800"
        title="Copy review note"
        data-testid="review-overview-copy"
        @click.stop="copyText(copyBody)"
      >
        <Icon name="copy" :size="12" class="shrink-0" />
        Copy
      </Button>
    </div>
    <article
      v-for="comment in (thread.comments || [])"
      :key="`review-overview-comment-${comment.id}`"
      class="mt-3 border-l-2 border-amber-300 pl-3"
    >
      <div class="flex items-center justify-between gap-2 text-[11px] text-slate-500">
        <span class="font-medium uppercase tracking-wide">{{ comment.author_kind }}</span>
        <span>{{ fmtLocalDateTime(comment.created_at) }}</span>
      </div>
      <ClampedText :lines="6" class="mt-1">
        <MarkdownContent :markdown="comment.body || ''" class="text-sm text-slate-800" />
      </ClampedText>
    </article>
  </div>
</template>
