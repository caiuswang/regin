<script setup>
import { computed } from 'vue'
import ProposalGeneralReviewNotes from './ProposalGeneralReviewNotes.vue'
import ProposalFeedbackThread from './ProposalFeedbackThread.vue'

// Content-card view of a draft topic's review threads. The right-hand sidebar
// (ProposalCommentsSidebar) still lists every thread; this surface is the
// de-duplicated companion — it shows only the most-recent review note and the
// most-recent comment expanded, folding the rest behind a <details>. Comments
// that used to live inline under the Intent/Aliases/Wiki sections are merged
// into one stream here (their anchor becomes the card title).
const props = defineProps({
  feedbackThreads: { type: Array, default: () => [] },
  selectedTopicId: { type: [String, Number], default: null },
  selectedTopicLabel: { type: String, default: '' },
})

const COMMENT_ANCHORS = ['proposal_summary', 'topic_field', 'wiki_range']

function sortByUpdatedDesc(list) {
  return [...list].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
}

const reviewNoteThreads = computed(() => sortByUpdatedDesc(
  props.feedbackThreads.filter((t) => !t.proposal_topic_id && t.anchor_kind === 'general'),
))

const commentThreads = computed(() => sortByUpdatedDesc(
  props.feedbackThreads.filter((t) =>
    t.proposal_topic_id === props.selectedTopicId && COMMENT_ANCHORS.includes(t.anchor_kind)),
))

function commentTitle(thread) {
  if (thread.anchor_kind === 'wiki_range') return 'Wiki preview comment'
  if (thread.anchor_kind === 'proposal_summary') return props.selectedTopicLabel || 'Topic summary'
  const field = thread.anchor?.field
  if (field === 'intent') return 'Intent comment'
  if (field === 'aliases') return 'Alias comment'
  return field ? `${field} comment` : 'Comment'
}
</script>

<template>
  <div class="space-y-5">
    <ProposalGeneralReviewNotes :threads="reviewNoteThreads" />

    <div v-if="commentThreads.length" class="space-y-2" data-testid="content-comments">
      <h3 class="topics-subsection-title">Comments</h3>
      <ProposalFeedbackThread
        :thread="commentThreads[0]"
        :title="commentTitle(commentThreads[0])"
        key-prefix="comment"
      />
      <details v-if="commentThreads.length > 1" class="mt-1">
        <summary class="cursor-pointer text-xs font-medium text-slate-500">
          Show {{ commentThreads.length - 1 }} more comment{{ commentThreads.length - 1 === 1 ? '' : 's' }}
        </summary>
        <div class="mt-2 space-y-2">
          <ProposalFeedbackThread
            v-for="thread in commentThreads.slice(1)"
            :key="`comment-${thread.id}`"
            :thread="thread"
            :title="commentTitle(thread)"
            key-prefix="comment"
          />
        </div>
      </details>
    </div>
  </div>
</template>
