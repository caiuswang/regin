<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../../api'
import { useConfirm } from '../../composables/useConfirm'
import Badge from '../Badge.vue'
import Card from '../Card.vue'
import MarkdownContent from '../MarkdownContent.vue'
import DiffPanel from './DiffPanel.vue'
import ProposalCommentsSidebar from './ProposalCommentsSidebar.vue'
import { fmtAgo, fmtLocalDateTime } from '../../utils/traceFormatters'

const props = defineProps({
  repo: { type: String, required: true },
  data: { type: Object, default: null },
  wikiLoaded: { type: Boolean, default: false },
  approvedTopicIds: { type: Array, default: () => [] },
})

const emit = defineEmits(['refresh', 'refresh-all', 'error'])

const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()

const busyAction = ref('')
const editingProposalTopicId = ref(null)
const proposalDraft = ref({})
const applyingTopicId = ref(null)
const commentsDrawerOpen = ref(false)

function startBusy(action) { busyAction.value = action }
function stopBusy() { busyAction.value = '' }
function isBusy(action = '') {
  if (!busyAction.value) return false
  return action ? busyAction.value === action : true
}

const runs = computed(() => props.data?.runs || [])
const selectedRun = computed(() => props.data?.selected_run || null)
// A run is stoppable only while its agent is still in flight.
const isActiveRun = computed(() =>
  ['queued', 'running', 'waiting_for_permission'].includes(selectedRun.value?.state)
)
const selectedProposalId = computed(() => props.data?.selected_proposal_id || '')
const selectedDraftTopic = computed(() => props.data?.selected_draft_topic || null)
const selectedDraftTopicId = computed(() => props.data?.selected_draft_topic_id || null)
const feedbackThreads = computed(() => props.data?.feedback_threads || [])

const selectedRunIndex = computed(() => runs.value.findIndex((r) => r.id === selectedProposalId.value))
const prevRun = computed(() => {
  const i = selectedRunIndex.value
  return i > 0 ? runs.value[i - 1] : null
})
const nextRun = computed(() => {
  const i = selectedRunIndex.value
  return i >= 0 && i < runs.value.length - 1 ? runs.value[i + 1] : null
})

const selectedProposalReviewState = computed(() =>
  selectedRun.value?.review_state || props.data?.proposal?.status || 'draft'
)
const proposalReadyToApply = computed(() =>
  ['ready_to_apply', 'partially_applied'].includes(selectedProposalReviewState.value)
)
const selectedRevision = computed(() => props.data?.selected_revision || null)
const selectedRevisionIsHistorical = computed(() =>
  Boolean(selectedRevision.value && !selectedRevision.value.is_latest)
)
const selectedRevisionIsLatest = computed(() => !selectedRevisionIsHistorical.value)

const selectedDraftFeedbackThreadCount = computed(() => {
  if (!selectedDraftTopicId.value) return 0
  return feedbackThreads.value.filter((t) => t.proposal_topic_id === selectedDraftTopicId.value).length
})
const selectedProposalThreads = computed(() => {
  return feedbackThreads.value.filter((t) => !t.proposal_topic_id || t.proposal_topic_id === selectedDraftTopicId.value)
})
const selectedTopicSummaryThreads = computed(() => selectedProposalThreads.value.filter((t) =>
  t.proposal_topic_id && t.anchor_kind === 'proposal_summary'
))
const selectedTopicIntentThreads = computed(() => selectedProposalThreads.value.filter((t) =>
  t.proposal_topic_id && t.anchor_kind === 'topic_field' && t.anchor?.field === 'intent'
))
const selectedTopicAliasesThreads = computed(() => selectedProposalThreads.value.filter((t) =>
  t.proposal_topic_id && t.anchor_kind === 'topic_field' && t.anchor?.field === 'aliases'
))
const selectedTopicWikiThreads = computed(() => selectedProposalThreads.value.filter((t) =>
  t.proposal_topic_id && t.anchor_kind === 'wiki_range'
))
const selectedGeneralReviewThreads = computed(() => selectedProposalThreads.value.filter((t) =>
  !t.proposal_topic_id && t.anchor_kind === 'general'
))
const selectedDraftAliases = computed(() => selectedDraftTopic.value?.aliases || [])
const selectedProposalFailure = computed(() =>
  props.data?.selected_status || props.data?.selected_run || null
)
const totalCommentsCount = computed(() =>
  (feedbackThreads.value || []).reduce((sum, t) => sum + (t.comments?.length || 0), 0)
)

function withQuery(next) {
  return { ...route.query, ...next }
}

function backToList() {
  router.replace({ query: withQuery({ tab: 'proposals', proposal: undefined, revision: undefined, draft: undefined }) })
}

function chooseRun(id) {
  if (!id) return
  router.replace({ query: withQuery({ tab: 'proposals', proposal: id, revision: undefined, draft: undefined }) })
}

function chooseRevision(revisionId) {
  router.replace({
    query: withQuery({
      tab: 'proposals',
      proposal: route.query.proposal,
      revision: revisionId || undefined,
      draft: undefined,
    }),
  })
}

function chooseDraftTopic(topicId) {
  router.replace({
    query: withQuery({
      tab: 'proposals',
      proposal: route.query.proposal,
      revision: route.query.revision,
      draft: topicId || undefined,
    }),
  })
}

async function regenerateProposal() {
  const proposalId = selectedProposalId.value
  if (!proposalId) return
  if (!selectedRevisionIsLatest.value) {
    emit('error', 'Switch to the latest revision before regenerating.')
    return
  }
  startBusy('regenerate-proposal')
  try {
    const result = await api.post(`/repos/${props.repo}/topics/proposals/${proposalId}/regenerate`, {})
    if (!result.ok) {
      emit('error', result.msg || result.error || 'Proposal regeneration failed')
      return
    }
    await router.replace({
      query: withQuery({ tab: 'proposals', proposal: proposalId, revision: undefined, draft: undefined }),
    })
    emit('refresh')
  } catch (err) {
    emit('error', err.message || String(err))
  } finally {
    stopBusy()
  }
}

async function deleteRun() {
  if (!selectedRun.value) return
  const ok = await confirm('Delete proposal run', `Delete proposal run ${selectedRun.value.id}?`, true)
  if (!ok) return
  startBusy('delete-proposal')
  try {
    const result = await api.post(`/repos/${props.repo}/topics/proposals/${selectedRun.value.id}/delete`, {})
    if (!result.ok) {
      emit('error', result.msg || result.error || 'Delete proposal failed')
      return
    }
    await router.replace({ query: withQuery({ tab: 'proposals', proposal: undefined, draft: undefined }) })
    emit('refresh')
  } catch (err) {
    emit('error', err.message || String(err))
  } finally {
    stopBusy()
  }
}

async function stopProposal() {
  if (!selectedRun.value) return
  const ok = await confirm(
    'Stop proposal run',
    `Stop the running proposal ${selectedRun.value.id}? The agent will be terminated and the run marked cancelled.`,
    true,
  )
  if (!ok) return
  startBusy('stop-proposal')
  try {
    const result = await api.post(`/repos/${props.repo}/topics/proposals/${selectedRun.value.id}/stop`, {})
    if (!result.ok) {
      emit('error', result.msg || result.error || 'Stop proposal failed')
      return
    }
    emit('refresh')
  } catch (err) {
    emit('error', err.message || String(err))
  } finally {
    stopBusy()
  }
}

async function updateReviewState(reviewState) {
  const proposalId = selectedProposalId.value
  if (!proposalId) return
  if (!selectedRevisionIsLatest.value) {
    emit('error', 'Only the latest revision can change review state.')
    return
  }
  startBusy(`proposal-review-state-${reviewState}`)
  try {
    const result = await api.post(`/repos/${props.repo}/topics/proposals/${proposalId}/review-state`, {
      review_state: reviewState,
    })
    if (!result.ok) {
      emit('error', result.msg || result.error || 'Review state update failed')
      return
    }
    emit('refresh')
  } catch (err) {
    emit('error', err.message || String(err))
  } finally {
    stopBusy()
  }
}

async function restoreRevision() {
  const proposalId = selectedProposalId.value
  const revisionId = selectedRevision.value?.id
  if (!proposalId || !revisionId) return
  startBusy('restore-revision')
  try {
    const result = await api.post(`/repos/${props.repo}/topics/proposals/${proposalId}/restore`, {
      revision_id: revisionId,
    })
    if (!result.ok) {
      emit('error', result.msg || result.error || 'Restore failed')
      return
    }
    // Navigate to the new latest revision (clearing the explicit revision
    // query so the page reloads in latest mode).
    router.replace({
      query: withQuery({ tab: 'proposals', proposal: proposalId, revision: undefined, draft: undefined }),
    })
    emit('refresh')
  } catch (err) {
    emit('error', err.message || String(err))
  } finally {
    stopBusy()
  }
}

function editProposedTopic(topic) {
  if (!selectedRevisionIsLatest.value) {
    emit('error', 'Historical revisions are read-only.')
    return
  }
  editingProposalTopicId.value = topic.id
  proposalDraft.value = {
    label: topic.label || '',
    aliases: (topic.aliases || []).join(', '),
    intent: topic.intent || '',
    include_globs: (topic.include_globs || []).join('\n'),
    exclude_globs: (topic.exclude_globs || []).join('\n'),
  }
}

function splitList(value, separator) {
  return String(value || '')
    .split(separator)
    .map((item) => item.trim())
    .filter(Boolean)
}

async function saveProposedTopic(topic) {
  const proposalId = selectedProposalId.value
  if (!proposalId) return
  if (!selectedRevisionIsLatest.value) {
    emit('error', 'Historical revisions are read-only.')
    return
  }
  startBusy('save-proposed-topic')
  try {
    const result = await api.post(`/repos/${props.repo}/topics/proposals/${proposalId}/topics/${topic.id}`, {
      label: proposalDraft.value.label,
      aliases: splitList(proposalDraft.value.aliases, ','),
      intent: proposalDraft.value.intent,
      include_globs: splitList(proposalDraft.value.include_globs, '\n'),
      exclude_globs: splitList(proposalDraft.value.exclude_globs, '\n'),
    })
    if (!result.ok) {
      emit('error', result.msg || 'Proposal update failed')
      return
    }
    editingProposalTopicId.value = null
    emit('refresh')
  } finally {
    stopBusy()
  }
}

async function ignoreProposedTopic(topic) {
  const proposalId = selectedProposalId.value
  if (!proposalId) return
  if (!selectedRevisionIsLatest.value) {
    emit('error', 'Historical revisions are read-only.')
    return
  }
  startBusy('ignore-proposed-topic')
  try {
    const result = await api.post(`/repos/${props.repo}/topics/proposals/${proposalId}/ignore`, {
      proposed_topic_id: topic.id,
    })
    if (!result.ok) {
      emit('error', result.msg || 'Proposal ignore failed')
      return
    }
    emit('refresh')
  } finally {
    stopBusy()
  }
}

function openApplyPanel(topic) {
  if (!selectedRevisionIsLatest.value) {
    emit('error', 'Historical revisions are read-only.')
    return
  }
  if (!proposalReadyToApply.value) {
    emit('error', 'Mark the proposal ready before applying any draft topics.')
    return
  }
  applyingTopicId.value = topic.id
}

function onDiffApplied() {
  applyingTopicId.value = null
  emit('refresh-all')
}

function onDiffCancelled() {
  applyingTopicId.value = null
}

watch(selectedProposalId, () => {
  applyingTopicId.value = null
  editingProposalTopicId.value = null
  commentsDrawerOpen.value = false
})

function proposalStateColor(state) {
  if (state === 'completed') return 'green'
  if (state === 'failed' || state === 'timed_out') return 'red'
  if (state === 'waiting_for_permission') return 'yellow'
  if (state === 'running' || state === 'queued') return 'blue'
  if (state === 'cancelled') return 'gray'
  return 'gray'
}

function reviewStatusColor(status) {
  if (status === 'accepted' || status === 'merged') return 'green'
  if (status === 'ignored') return 'gray'
  return 'blue'
}

function proposalReviewColor(status) {
  if (status === 'ready_to_apply') return 'green'
  if (status === 'changes_requested') return 'yellow'
  if (status === 'partially_applied') return 'blue'
  if (status === 'applied') return 'green'
  return 'gray'
}

function feedbackThreadColor(status) {
  if (status === 'addressed' || status === 'resolved') return 'green'
  if (status === 'dismissed') return 'gray'
  return 'yellow'
}
</script>

<template>
  <div v-if="!selectedRun" class="topics-runs-empty">
    <p class="text-sm text-slate-600">
      Run <code class="text-xs">{{ route.query.proposal }}</code> not found.
    </p>
    <button type="button" class="btn btn-secondary mt-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" @click="backToList">← Back to runs</button>
  </div>
  <div v-else class="topics-run-detail">
    <Card>
      <div class="topics-run-header-strip">
        <div class="topics-run-header-left">
          <button
            type="button"
            class="topics-back-link focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            @click="backToList"
          >← Back to runs</button>
          <h2 class="topics-run-title" :title="selectedRun.topic_request || ''">{{ selectedRun.title || 'Untitled run' }}</h2>
          <div class="topics-run-header-id">
            <code class="text-xs text-slate-500">{{ selectedRun.id }}</code>
            <span v-if="fmtAgo(selectedRun.last_activity_at)" class="text-xs text-slate-500">· updated {{ fmtAgo(selectedRun.last_activity_at) }}</span>
            <Badge :color="proposalStateColor(selectedRun.state)" :label="selectedRun.state || 'completed'" />
            <span class="text-xs text-slate-500">{{ selectedRun.provider }}<span v-if="selectedRun.agent"> · {{ selectedRun.agent }}</span></span>
            <Badge
              :color="proposalReviewColor(selectedProposalReviewState)"
              :label="selectedProposalReviewState.replaceAll('_', ' ')"
            />
            <Badge
              v-if="selectedRevision"
              :color="selectedRevisionIsLatest ? 'blue' : 'gray'"
              :label="`r${selectedRevision.revision_number}${selectedRevisionIsLatest ? ' latest' : ''}`"
            />
          </div>
        </div>
        <div class="topics-run-header-actions btn-row">
          <button
            type="button"
            class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="!prevRun"
            :title="prevRun ? `Previous: ${prevRun.id}` : 'No earlier run'"
            @click="chooseRun(prevRun?.id)"
          >← Prev</button>
          <button
            type="button"
            class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="!nextRun"
            :title="nextRun ? `Next: ${nextRun.id}` : 'No later run'"
            @click="chooseRun(nextRun?.id)"
          >Next →</button>
          <span class="topics-run-header-divider" aria-hidden="true"></span>
          <button
            v-if="isActiveRun"
            type="button"
            class="btn btn-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="isBusy()"
            title="Terminate the running agent and cancel this run"
            @click="stopProposal"
          >{{ isBusy('stop-proposal') ? 'Stopping…' : 'Stop' }}</button>
          <button
            v-if="!['applied', 'partially_applied'].includes(selectedProposalReviewState)"
            type="button"
            class="btn btn-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="isBusy() || !selectedRevisionIsLatest"
            @click="updateReviewState('changes_requested')"
          >Request changes</button>
          <button
            v-if="!['ready_to_apply', 'partially_applied', 'applied'].includes(selectedProposalReviewState)"
            type="button"
            class="btn btn-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="isBusy() || !selectedRevisionIsLatest"
            @click="updateReviewState('ready_to_apply')"
          >Mark ready</button>
          <button
            type="button"
            class="btn btn-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="isBusy() || !selectedProposalId || !selectedRevisionIsLatest"
            @click="regenerateProposal"
          >Regenerate</button>
          <button
            type="button"
            class="btn btn-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :disabled="isBusy()"
            @click="deleteRun"
          >Delete Run</button>
        </div>
      </div>
    </Card>

    <Card :no-padding="true">
      <div class="topics-panel-header">
        <div>
          <h2>Draft Topics</h2>
          <p class="topics-panel-caption">Review each proposed topic before accepting, merging, or ignoring it.</p>
        </div>
        <Badge color="purple" :label="String((data?.draft_topics || []).length)" />
      </div>
      <table class="tbl tbl-workbench">
        <thead>
          <tr>
            <th>Label</th>
            <th>Status</th>
            <th class="text-right">Evidence</th>
            <th class="text-right">Refs</th>
            <th class="text-right">Threads</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="topic in (data?.draft_topics || [])"
            :key="topic.id"
            class="topics-row-selectable cursor-pointer"
            :class="{ 'tbl-row-active': topic.id === selectedDraftTopicId }"
            @click="chooseDraftTopic(topic.id)"
          >
            <td>
              <button type="button" class="topics-row-button focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" @click.stop="chooseDraftTopic(topic.id)">
                <div class="topics-row-title">{{ topic.label }}</div>
                <div class="topics-row-meta line-clamp-2">{{ topic.intent_preview }}</div>
              </button>
            </td>
            <td><Badge :color="reviewStatusColor(topic.review_status)" :label="topic.review_status" /></td>
            <td class="text-right">{{ topic.evidence_count }}</td>
            <td class="text-right">{{ topic.proposed_ref_count }}</td>
            <td class="text-right">{{ topic.feedback_thread_count || 0 }}</td>
          </tr>
          <tr v-if="!(data?.draft_topics || []).length">
            <td colspan="5" class="text-gray-500">No draft topics in this run.</td>
          </tr>
        </tbody>
      </table>
    </Card>

    <div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <Card>
        <div v-if="selectedDraftTopic" class="space-y-5">
          <div class="topics-detail-header">
            <div class="topics-detail-copy">
              <div class="flex items-center justify-between gap-2 flex-wrap">
                <p class="topics-detail-eyebrow">Draft Topic</p>
                <button
                  type="button"
                  class="btn btn-secondary text-xs xl:hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                  @click="commentsDrawerOpen = true"
                >Comments ({{ totalCommentsCount }})</button>
              </div>
              <div class="flex flex-wrap items-center gap-2">
                <h2 class="topics-detail-title">{{ selectedDraftTopic.label }}</h2>
                <Badge color="purple" :label="`${selectedDraftFeedbackThreadCount} thread${selectedDraftFeedbackThreadCount === 1 ? '' : 's'}`" />
              </div>
              <p class="text-sm text-gray-600">{{ selectedDraftTopic.intent }}</p>
              <p
                v-if="!proposalReadyToApply
                      && (!selectedDraftTopic.review_status
                          || selectedDraftTopic.review_status === 'pending')"
                class="text-xs text-amber-700 mt-2"
              >
                Review is still in progress. Mark the proposal ready before applying draft topics.
              </p>
              <div v-if="selectedRevisionIsHistorical" class="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <span>You are viewing a historical revision. Editing, review-state changes, apply, and new comments are disabled here.</span>
                <button
                  type="button"
                  class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                  @click="chooseRevision(undefined)"
                >Back to latest revision</button>
                <button
                  type="button"
                  class="btn btn-primary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                  :disabled="isBusy('restore-revision')"
                  :title="`Append a new revision based on r${selectedRevision?.revision_number}`"
                  @click="restoreRevision"
                >{{ isBusy('restore-revision') ? 'Restoring…' : `Restore this revision` }}</button>
              </div>
            </div>
            <div class="topics-detail-actions">
              <div class="topics-detail-status-row">
                <Badge :color="reviewStatusColor(selectedDraftTopic.review_status || 'pending')" :label="selectedDraftTopic.review_status || 'pending'" />
              </div>
              <div class="topics-detail-button-row btn-row">
                <button
                  v-if="selectedDraftTopic.review_status !== 'accepted'"
                  type="button"
                  class="btn btn-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                  :disabled="isBusy() || !selectedRevisionIsLatest"
                  @click="editProposedTopic(selectedDraftTopic)"
                >Edit</button>
                <button
                  v-if="(!selectedDraftTopic.review_status
                        || selectedDraftTopic.review_status === 'pending')
                        && applyingTopicId !== selectedDraftTopic.id"
                  type="button"
                  class="btn btn-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                  :disabled="isBusy() || !proposalReadyToApply || !selectedRevisionIsLatest"
                  data-testid="apply-proposed-topic"
                  @click="openApplyPanel(selectedDraftTopic)"
                >Apply</button>
                <button
                  v-if="!selectedDraftTopic.review_status"
                  type="button"
                  class="btn btn-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                  :disabled="isBusy() || !selectedRevisionIsLatest"
                  @click="ignoreProposedTopic(selectedDraftTopic)"
                >Ignore</button>
              </div>
            </div>
          </div>

          <div v-if="selectedGeneralReviewThreads.length" class="space-y-3">
            <h3 class="topics-subsection-title">General Review Notes</h3>
            <div
              v-for="thread in selectedGeneralReviewThreads"
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

          <div v-if="data?.revisions?.length" class="topics-candidate-card">
            <h3 class="topics-subsection-title">Revision History</h3>
            <p class="text-sm text-slate-600 mb-3">
              Browse previous revisions to compare drafts. Historical revisions are read-only.
            </p>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="revision in data.revisions"
                :key="revision.id"
                type="button"
                class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
                :class="{ 'border-blue-300 bg-blue-50 text-blue-900': revision.id === data.selected_revision_id }"
                @click="chooseRevision(revision.id)"
              >
                r{{ revision.revision_number }} · {{ revision.kind }}
                <span v-if="revision.kind === 'restored' && revision.metadata?.restored_from_revision_number" class="ml-1 text-slate-500">
                  from r{{ revision.metadata.restored_from_revision_number }}
                </span>
                <span v-if="revision.kind === 'downgraded' && revision.metadata?.downgraded_from_topic_id" class="ml-1 text-slate-500">
                  from approved graph
                </span>
                <span v-if="revision.is_latest" class="ml-1 text-slate-500">latest</span>
              </button>
            </div>
          </div>

          <DiffPanel
            v-if="applyingTopicId === selectedDraftTopic.id"
            :repo-name="repo"
            :proposal-id="selectedProposalId"
            :topic="selectedDraftTopic"
            :approved-topic-ids="approvedTopicIds"
            @applied="onDiffApplied"
            @cancelled="onDiffCancelled"
          />

          <div v-if="editingProposalTopicId === selectedDraftTopic.id" class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <label class="text-xs text-gray-500">
              Label
              <input v-model="proposalDraft.label" aria-label="Draft topic label" class="mt-1 w-full topics-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2">
            </label>
            <label class="text-xs text-gray-500">
              Aliases
              <input v-model="proposalDraft.aliases" aria-label="Draft topic aliases" class="mt-1 w-full topics-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2">
            </label>
            <label class="md:col-span-2 text-xs text-gray-500">
              Intent
              <textarea v-model="proposalDraft.intent" rows="2" aria-label="Draft topic intent" class="mt-1 w-full topics-input focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"></textarea>
            </label>
            <label class="text-xs text-gray-500">
              Include globs
              <textarea v-model="proposalDraft.include_globs" rows="3" aria-label="Include globs" class="mt-1 w-full topics-input font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"></textarea>
            </label>
            <label class="text-xs text-gray-500">
              Exclude globs
              <textarea v-model="proposalDraft.exclude_globs" rows="3" aria-label="Exclude globs" class="mt-1 w-full topics-input font-mono focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"></textarea>
            </label>
            <div class="md:col-span-2 btn-row">
              <button type="button" class="btn btn-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" :disabled="isBusy() || !selectedRevisionIsLatest" @click="saveProposedTopic(selectedDraftTopic)">Save</button>
              <button type="button" class="btn btn-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" :disabled="isBusy()" @click="editingProposalTopicId = null">Cancel</button>
            </div>
          </div>

          <div v-if="!wikiLoaded" class="topics-candidate-card">
            <h3 class="topics-subsection-title">Merge Target</h3>
            <p class="text-sm text-gray-600">Merge uses the topic selected in Approved mode. Open Approved once to choose a target topic.</p>
          </div>

          <div v-if="selectedTopicSummaryThreads.length" class="space-y-3">
            <h3 class="topics-subsection-title">Inline review: topic summary</h3>
            <div
              v-for="thread in selectedTopicSummaryThreads"
              :key="`summary-${thread.id}`"
              class="rounded border border-amber-200 bg-amber-50/70 px-4 py-3"
            >
              <div class="flex flex-wrap items-center gap-2">
                <Badge :color="feedbackThreadColor(thread.resolution_state)" :label="thread.resolution_state || 'open'" />
                <span class="text-xs font-medium text-slate-700">{{ selectedDraftTopic.label }}</span>
                <span v-if="thread.addressed_in_revision_number" class="text-[11px] text-slate-500">addressed in r{{ thread.addressed_in_revision_number }}</span>
              </div>
              <p v-if="thread.quoted_text" class="mt-2 text-xs text-slate-500">“{{ thread.quoted_text }}”</p>
              <article
                v-for="comment in (thread.comments || [])"
                :key="`summary-comment-${comment.id}`"
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

          <div class="space-y-2">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="topics-subsection-title !mb-0">Intent</h3>
              <Badge
                v-if="selectedTopicIntentThreads.length"
                color="yellow"
                :label="`${selectedTopicIntentThreads.length} comment${selectedTopicIntentThreads.length === 1 ? '' : 's'}`"
              />
            </div>
            <p class="text-sm text-slate-700">{{ selectedDraftTopic.intent }}</p>
            <div
              v-for="thread in selectedTopicIntentThreads"
              :key="`intent-${thread.id}`"
              class="rounded border border-amber-200 bg-amber-50/70 px-4 py-3"
            >
              <div class="flex flex-wrap items-center gap-2">
                <Badge :color="feedbackThreadColor(thread.resolution_state)" :label="thread.resolution_state || 'open'" />
                <span class="text-xs font-medium text-slate-700">Intent comment</span>
                <span v-if="thread.addressed_in_revision_number" class="text-[11px] text-slate-500">addressed in r{{ thread.addressed_in_revision_number }}</span>
              </div>
              <p v-if="thread.quoted_text" class="mt-2 text-xs text-slate-500">“{{ thread.quoted_text }}”</p>
              <article
                v-for="comment in (thread.comments || [])"
                :key="`intent-comment-${comment.id}`"
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

          <div class="space-y-2">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="topics-subsection-title !mb-0">Aliases</h3>
              <Badge
                v-if="selectedTopicAliasesThreads.length"
                color="yellow"
                :label="`${selectedTopicAliasesThreads.length} comment${selectedTopicAliasesThreads.length === 1 ? '' : 's'}`"
              />
            </div>
            <div v-if="selectedDraftAliases.length" class="flex flex-wrap gap-2">
              <code v-for="alias in selectedDraftAliases" :key="alias" class="text-xs">{{ alias }}</code>
            </div>
            <p v-else class="text-sm text-slate-500">No aliases proposed for this topic.</p>
            <div
              v-for="thread in selectedTopicAliasesThreads"
              :key="`aliases-${thread.id}`"
              class="rounded border border-amber-200 bg-amber-50/70 px-4 py-3"
            >
              <div class="flex flex-wrap items-center gap-2">
                <Badge :color="feedbackThreadColor(thread.resolution_state)" :label="thread.resolution_state || 'open'" />
                <span class="text-xs font-medium text-slate-700">Alias comment</span>
                <span v-if="thread.addressed_in_revision_number" class="text-[11px] text-slate-500">addressed in r{{ thread.addressed_in_revision_number }}</span>
              </div>
              <p v-if="thread.quoted_text" class="mt-2 text-xs text-slate-500">“{{ thread.quoted_text }}”</p>
              <article
                v-for="comment in (thread.comments || [])"
                :key="`aliases-comment-${comment.id}`"
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

          <div>
            <h3 class="topics-subsection-title">Evidence Paths</h3>
            <div class="flex flex-wrap gap-2">
              <code v-for="path in (selectedDraftTopic.evidence_paths || [])" :key="path" class="text-xs">{{ path }}</code>
            </div>
          </div>

          <div v-if="selectedProposalFailure?.error || selectedProposalFailure?.error_detail || selectedProposalFailure?.stdout_tail || selectedProposalFailure?.stderr_tail" class="topics-failure-box">
            <h3 class="topics-subsection-title !mb-2">Failure Details</h3>
            <p v-if="selectedProposalFailure?.error" class="text-sm text-red-800 mb-2">{{ selectedProposalFailure.error }}</p>
            <pre v-if="selectedProposalFailure?.error_detail" class="topics-log">{{ selectedProposalFailure.error_detail }}</pre>
            <details v-if="selectedProposalFailure?.stdout_tail">
              <summary class="text-xs font-medium text-red-900">stdout tail</summary>
              <pre class="topics-log mt-2">{{ selectedProposalFailure.stdout_tail }}</pre>
            </details>
            <details v-if="selectedProposalFailure?.stderr_tail">
              <summary class="text-xs font-medium text-red-900">stderr tail</summary>
              <pre class="topics-log mt-2">{{ selectedProposalFailure.stderr_tail }}</pre>
            </details>
          </div>

          <div v-if="data?.wiki_preview" class="topics-markdown">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="topics-subsection-title !mb-0">Wiki Preview</h3>
              <Badge
                v-if="selectedTopicWikiThreads.length"
                color="yellow"
                :label="`${selectedTopicWikiThreads.length} comment${selectedTopicWikiThreads.length === 1 ? '' : 's'}`"
              />
            </div>
            <div
              v-for="thread in selectedTopicWikiThreads"
              :key="`wiki-${thread.id}`"
              class="mb-4 rounded border border-amber-200 bg-amber-50/70 px-4 py-3"
            >
              <div class="flex flex-wrap items-center gap-2">
                <Badge :color="feedbackThreadColor(thread.resolution_state)" :label="thread.resolution_state || 'open'" />
                <span class="text-xs font-medium text-slate-700">Wiki preview comment</span>
                <span v-if="thread.addressed_in_revision_number" class="text-[11px] text-slate-500">addressed in r{{ thread.addressed_in_revision_number }}</span>
              </div>
              <p v-if="thread.quoted_text" class="mt-2 text-xs text-slate-500">“{{ thread.quoted_text }}”</p>
              <article
                v-for="comment in (thread.comments || [])"
                :key="`wiki-comment-${comment.id}`"
                class="mt-3 border-l-2 border-amber-300 pl-3"
              >
                <div class="flex items-center justify-between gap-2 text-[11px] text-slate-500">
                  <span class="font-medium uppercase tracking-wide">{{ comment.author_kind }}</span>
                  <span>{{ fmtLocalDateTime(comment.created_at) }}</span>
                </div>
                <p class="mt-1 whitespace-pre-wrap text-sm text-slate-800">{{ comment.body }}</p>
              </article>
            </div>
            <MarkdownContent :markdown="data.wiki_preview" />
          </div>
        </div>
        <p v-else class="text-sm text-gray-500">Select a proposal draft topic to review its evidence and actions.</p>
      </Card>

      <Card class="hidden xl:block">
        <ProposalCommentsSidebar
          :repo-name="repo"
          :proposal-id="selectedProposalId"
          :selected-topic="selectedDraftTopic"
          :threads="feedbackThreads"
          :readonly="!selectedRevisionIsLatest"
          @updated="emit('refresh')"
        />
      </Card>
    </div>

    <transition name="topics-drawer">
      <div
        v-if="commentsDrawerOpen"
        class="topics-comments-drawer-backdrop xl:hidden cursor-pointer hover:bg-slate-900/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
        role="button"
        aria-label="Close comments drawer"
        tabindex="0"
        @click="commentsDrawerOpen = false"
        @keydown.esc="commentsDrawerOpen = false"
      >
        <aside
          class="topics-comments-drawer"
          role="dialog"
          aria-label="Review comments"
          @click.stop
        >
          <header class="topics-comments-drawer-header">
            <h3 class="text-sm font-semibold text-slate-700">Review comments</h3>
            <button
              type="button"
              class="btn btn-secondary text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
              @click="commentsDrawerOpen = false"
            >Close</button>
          </header>
          <div class="topics-comments-drawer-body">
            <ProposalCommentsSidebar
              :repo-name="repo"
              :proposal-id="selectedProposalId"
              :selected-topic="selectedDraftTopic"
              :threads="feedbackThreads"
              :readonly="!selectedRevisionIsLatest"
              @updated="emit('refresh')"
            />
          </div>
        </aside>
      </div>
    </transition>
  </div>
</template>
