<script setup>
import { computed, ref, watch } from 'vue'
import api from '../../api'
import { useConfirm } from '../../composables/useConfirm'
import { fmtLocalDateTime } from '../../utils/traceFormatters'
import Badge from '../Badge.vue'

const { confirm } = useConfirm()

const props = defineProps({
  repoName: { type: String, required: true },
  proposalId: { type: String, default: '' },
  selectedTopic: { type: Object, default: null },
  threads: { type: Array, default: () => [] },
  readonly: { type: Boolean, default: false },
})

const emit = defineEmits(['updated'])

const filterMode = ref('selected')
const composerAnchor = ref('general')
const composerBody = ref('')
const composerBusy = ref(false)
const composerError = ref('')
const replyingThreadId = ref(null)
const replyBusy = ref(false)
const replyDrafts = ref({})
const resolveBusyThreadId = ref(null)
const editingCommentId = ref(null)
const editDraft = ref('')
const commentBusy = ref(false)

const anchorOptions = computed(() => {
  const options = [
    {
      value: 'general',
      label: 'General review note',
      anchorKind: 'general',
      proposalTopicId: null,
      anchor: { section: 'revision-overview' },
      quotedText: '',
    },
  ]
  if (!props.selectedTopic) return options
  options.push(
    {
      value: 'topic-summary',
      label: 'Selected topic summary',
      anchorKind: 'proposal_summary',
      proposalTopicId: props.selectedTopic.id,
      anchor: { topic_id: props.selectedTopic.id, section: 'summary' },
      quotedText: props.selectedTopic.label || props.selectedTopic.id,
    },
    {
      value: 'topic-intent',
      label: 'Selected topic intent',
      anchorKind: 'topic_field',
      proposalTopicId: props.selectedTopic.id,
      anchor: { topic_id: props.selectedTopic.id, field: 'intent' },
      quotedText: props.selectedTopic.intent || '',
    },
    {
      value: 'topic-aliases',
      label: 'Selected topic aliases',
      anchorKind: 'topic_field',
      proposalTopicId: props.selectedTopic.id,
      anchor: { topic_id: props.selectedTopic.id, field: 'aliases' },
      quotedText: (props.selectedTopic.aliases || []).join(', '),
    },
    {
      value: 'wiki-preview',
      label: 'Wiki preview',
      anchorKind: 'wiki_range',
      proposalTopicId: props.selectedTopic.id,
      anchor: { topic_id: props.selectedTopic.id, section: 'wiki-preview' },
      quotedText: `Wiki preview for ${props.selectedTopic.label || props.selectedTopic.id}`,
    },
  )
  return options
})

const activeAnchorOption = computed(() => (
  anchorOptions.value.find((option) => option.value === composerAnchor.value) || anchorOptions.value[0]
))

const sortedThreads = computed(() => {
  return [...props.threads].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
})

const visibleThreads = computed(() => {
  if (filterMode.value !== 'selected' || !props.selectedTopic) return sortedThreads.value
  return sortedThreads.value.filter((thread) => !thread.proposal_topic_id || thread.proposal_topic_id === props.selectedTopic.id)
})

const selectedTopicThreadCount = computed(() => {
  if (!props.selectedTopic) return 0
  return props.threads.filter((thread) => thread.proposal_topic_id === props.selectedTopic.id).length
})

function threadTargetLabel(thread) {
  if (thread.proposal_topic_id) {
    if (thread.anchor_kind === 'topic_field') {
      const field = thread.anchor?.field || 'field'
      return `${thread.proposal_topic_id} · ${field}`
    }
    if (thread.anchor_kind === 'wiki_range') return `${thread.proposal_topic_id} · wiki`
    if (thread.anchor_kind === 'proposal_summary') return `${thread.proposal_topic_id} · summary`
    return thread.proposal_topic_id
  }
  return 'General'
}

function threadTone(thread) {
  if (thread.resolution_state === 'addressed' || thread.resolution_state === 'resolved') return 'green'
  if (thread.resolution_state === 'dismissed') return 'gray'
  return 'yellow'
}

function isResolvedThread(thread) {
  return thread.resolution_state === 'resolved' || thread.resolution_state === 'dismissed'
}

function startReply(threadId) {
  replyingThreadId.value = threadId
  if (!replyDrafts.value[threadId]) replyDrafts.value[threadId] = ''
}

function cancelReply() {
  replyingThreadId.value = null
}

async function createThread() {
  if (!props.proposalId || composerBusy.value) return
  const body = composerBody.value.trim()
  if (!body) {
    composerError.value = 'Comment body is required.'
    return
  }
  composerBusy.value = true
  composerError.value = ''
  try {
    const option = activeAnchorOption.value
    const res = await api.post(`/repos/${props.repoName}/topics/proposals/${props.proposalId}/feedback-threads`, {
      proposal_topic_id: option.proposalTopicId,
      kind: 'comment',
      anchor_kind: option.anchorKind,
      anchor: option.anchor,
      quoted_text: option.quotedText,
      body,
      author_kind: 'user',
    })
    if (res?.ok) {
      composerBody.value = ''
      emit('updated')
      return
    }
    composerError.value = res?.msg || res?.error || 'Failed to add comment thread'
  } catch (err) {
    composerError.value = err?.message || String(err)
  } finally {
    composerBusy.value = false
  }
}

async function submitReply(threadId) {
  if (!props.proposalId || replyBusy.value) return
  const body = String(replyDrafts.value[threadId] || '').trim()
  if (!body) return
  replyBusy.value = true
  composerError.value = ''
  try {
    const res = await api.post(
      `/repos/${props.repoName}/topics/proposals/${props.proposalId}/feedback-threads/${threadId}/comments`,
      { body, author_kind: 'user' },
    )
    if (res?.ok) {
      replyDrafts.value[threadId] = ''
      replyingThreadId.value = null
      emit('updated')
      return
    }
    composerError.value = res?.msg || res?.error || 'Failed to reply'
  } catch (err) {
    composerError.value = err?.message || String(err)
  } finally {
    replyBusy.value = false
  }
}

async function setResolution(threadId, resolutionState) {
  if (!props.proposalId || resolveBusyThreadId.value) return
  resolveBusyThreadId.value = threadId
  composerError.value = ''
  try {
    const res = await api.post(
      `/repos/${props.repoName}/topics/proposals/${props.proposalId}/feedback-threads/${threadId}/resolution`,
      { resolution_state: resolutionState },
    )
    if (res?.ok) {
      emit('updated')
      return
    }
    composerError.value = res?.msg || res?.error || 'Failed to update thread'
  } catch (err) {
    composerError.value = err?.message || String(err)
  } finally {
    resolveBusyThreadId.value = null
  }
}

function startEditComment(comment) {
  editingCommentId.value = comment.id
  editDraft.value = comment.body || ''
  replyingThreadId.value = null
}

function cancelEditComment() {
  editingCommentId.value = null
  editDraft.value = ''
}

async function saveEditComment(threadId, commentId) {
  if (!props.proposalId || commentBusy.value) return
  const body = editDraft.value.trim()
  if (!body) return
  commentBusy.value = true
  composerError.value = ''
  try {
    const res = await api.post(
      `/repos/${props.repoName}/topics/proposals/${props.proposalId}/feedback-threads/${threadId}/comments/${commentId}/update`,
      { body },
    )
    if (res?.ok) {
      cancelEditComment()
      emit('updated')
      return
    }
    composerError.value = res?.msg || res?.error || 'Failed to edit comment'
  } catch (err) {
    composerError.value = err?.message || String(err)
  } finally {
    commentBusy.value = false
  }
}

async function deleteComment(threadId, commentId, isLastComment) {
  if (!props.proposalId || commentBusy.value) return
  const message = isLastComment
    ? 'Delete this comment? It is the only comment, so the whole thread will be removed.'
    : 'Delete this comment?'
  if (!(await confirm('Delete comment', message, true))) return
  commentBusy.value = true
  composerError.value = ''
  try {
    const res = await api.post(
      `/repos/${props.repoName}/topics/proposals/${props.proposalId}/feedback-threads/${threadId}/comments/${commentId}/delete`,
      {},
    )
    if (res?.ok) {
      if (editingCommentId.value === commentId) cancelEditComment()
      emit('updated')
      return
    }
    composerError.value = res?.msg || res?.error || 'Failed to delete comment'
  } catch (err) {
    composerError.value = err?.message || String(err)
  } finally {
    commentBusy.value = false
  }
}

watch(
  () => props.selectedTopic?.id,
  () => {
    if (filterMode.value === 'selected' && !props.selectedTopic) filterMode.value = 'all'
    composerAnchor.value = 'general'
    composerError.value = ''
    replyingThreadId.value = null
    cancelEditComment()
  },
)
</script>

<template>
  <section class="space-y-4" data-testid="proposal-comments-sidebar">
    <header class="space-y-1">
      <div class="flex items-center justify-between gap-2">
        <div>
          <h3 class="text-base font-semibold text-slate-900">Review comments</h3>
          <p class="text-xs text-slate-500">Keep discussion in sidebar threads instead of editing review notes into the draft body.</p>
        </div>
        <Badge color="purple" :label="String(threads.length)" />
      </div>
      <div class="flex flex-wrap gap-2 text-xs">
        <button
          type="button"
          class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          :class="{ 'border-blue-300 bg-blue-50 text-blue-900': filterMode === 'all' }"
          @click="filterMode = 'all'"
        >
          All threads
        </button>
        <button
          v-if="selectedTopic"
          type="button"
          class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          :class="{ 'border-blue-300 bg-blue-50 text-blue-900': filterMode === 'selected' }"
          @click="filterMode = 'selected'"
        >
          Selected topic
          <span class="ml-1 text-slate-500">({{ selectedTopicThreadCount }})</span>
        </button>
      </div>
    </header>

    <div class="space-y-2 rounded border border-slate-200 bg-slate-50 p-3">
      <label class="block text-xs text-slate-600">
        Anchor
        <select
          v-model="composerAnchor"
          class="mt-1 w-full topics-input text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
        >
          <option v-for="option in anchorOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
      <label class="block text-xs text-slate-600">
        Comment
        <textarea
          v-model="composerBody"
          rows="4"
          class="mt-1 w-full topics-input text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          placeholder="Add review guidance, a concern, or regenerate instructions"
        />
      </label>
      <p v-if="composerError" class="text-xs text-red-700">{{ composerError }}</p>
      <p v-if="readonly" class="text-xs text-slate-500">
        Historical revisions are read-only. Switch back to the latest revision to add comments or replies.
      </p>
      <div v-else class="flex justify-end">
        <button
          type="button"
          class="btn btn-primary text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          :disabled="composerBusy || !proposalId"
          @click="createThread"
        >
          {{ composerBusy ? 'Adding…' : 'Add thread' }}
        </button>
      </div>
    </div>

    <div v-if="!visibleThreads.length" class="rounded border border-dashed border-slate-300 bg-white px-4 py-5 text-sm text-slate-500">
      No review threads yet.
    </div>

    <div v-for="thread in visibleThreads" :key="thread.id" class="rounded border border-slate-200 bg-white p-3 space-y-3">
      <div class="flex items-start justify-between gap-3">
        <div class="space-y-1 min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <Badge :color="threadTone(thread)" :label="thread.resolution_state || 'open'" />
            <span class="text-xs font-medium text-slate-700">{{ threadTargetLabel(thread) }}</span>
            <span v-if="thread.revision_number" class="text-[11px] text-slate-500">
              opened in r{{ thread.revision_number }}
            </span>
            <span v-if="thread.resolution_state === 'addressed' && thread.addressed_in_revision_number" class="text-[11px] text-slate-500">
              addressed in r{{ thread.addressed_in_revision_number }}
            </span>
          </div>
          <p v-if="thread.quoted_text" class="text-xs text-slate-500 line-clamp-3">“{{ thread.quoted_text }}”</p>
        </div>
        <div class="text-[11px] text-slate-400 whitespace-nowrap">{{ fmtLocalDateTime(thread.updated_at) }}</div>
      </div>

      <div class="space-y-2">
        <article v-for="comment in (thread.comments || [])" :key="comment.id" class="rounded border border-slate-200 bg-slate-50 px-3 py-2">
          <div class="flex items-center justify-between gap-2 text-[11px] text-slate-500">
            <span class="font-medium uppercase tracking-wide">{{ comment.author_kind }}</span>
            <span>{{ fmtLocalDateTime(comment.created_at) }}</span>
          </div>
          <template v-if="editingCommentId === comment.id && !readonly">
            <textarea
              v-model="editDraft"
              rows="3"
              class="mt-1 w-full topics-input text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            />
            <div class="mt-1 flex justify-end gap-2">
              <button
                type="button"
                class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                :disabled="commentBusy"
                @click="cancelEditComment"
              >
                Cancel
              </button>
              <button
                type="button"
                class="btn btn-primary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                :disabled="commentBusy || !editDraft.trim()"
                @click="saveEditComment(thread.id, comment.id)"
              >
                {{ commentBusy ? 'Saving…' : 'Save' }}
              </button>
            </div>
          </template>
          <template v-else>
            <p class="mt-1 whitespace-pre-wrap text-sm text-slate-800">{{ comment.body }}</p>
            <div v-if="!readonly" class="mt-1 flex justify-end gap-2">
              <button
                type="button"
                class="text-[11px] text-slate-500 hover:text-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                @click="startEditComment(comment)"
              >
                Edit
              </button>
              <button
                type="button"
                class="text-[11px] text-slate-500 hover:text-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                :disabled="commentBusy"
                @click="deleteComment(thread.id, comment.id, (thread.comments || []).length === 1)"
              >
                Delete
              </button>
            </div>
          </template>
        </article>
      </div>

       <div v-if="replyingThreadId === thread.id && !readonly" class="space-y-2">
        <textarea
          v-model="replyDrafts[thread.id]"
          rows="3"
          class="w-full topics-input text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          placeholder="Reply to this thread"
        />
        <div class="flex justify-end gap-2">
          <button
            type="button"
            class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="replyBusy"
            @click="cancelReply"
          >
            Cancel
          </button>
          <button
            type="button"
            class="btn btn-primary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="replyBusy"
            @click="submitReply(thread.id)"
          >
            {{ replyBusy ? 'Replying…' : 'Reply' }}
          </button>
        </div>
      </div>
       <div v-else-if="!readonly" class="flex justify-end gap-2">
        <button
          v-if="!isResolvedThread(thread)"
          type="button"
          class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          :disabled="resolveBusyThreadId === thread.id"
          @click="setResolution(thread.id, 'resolved')"
        >
          {{ resolveBusyThreadId === thread.id ? 'Resolving…' : 'Resolve' }}
        </button>
        <button
          v-else
          type="button"
          class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          :disabled="resolveBusyThreadId === thread.id"
          @click="setResolution(thread.id, 'open')"
        >
          {{ resolveBusyThreadId === thread.id ? 'Reopening…' : 'Reopen' }}
        </button>
        <button
          type="button"
          class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          @click="startReply(thread.id)"
        >
          Reply
        </button>
      </div>
    </div>
  </section>
</template>
