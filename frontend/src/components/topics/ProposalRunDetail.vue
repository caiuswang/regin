<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../../api'
import { useConfirm } from '../../composables/useConfirm'
import { useProposalApplyAll } from '../../composables/useProposalApplyAll'
import { topicReviewStatusColor } from '../../composables/useBadgeColor'
import { isPendingTopic } from '../../utils/proposalApply'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Select from '../ui/Select.vue'
import Card from '../Card.vue'
import MarkdownContent from '../MarkdownContent.vue'
import DiffPanel from './DiffPanel.vue'
import ProposalCommentsSidebar from './ProposalCommentsSidebar.vue'
import ProposalRunHeader from './ProposalRunHeader.vue'
import ProposalDraftTopicsTable from './ProposalDraftTopicsTable.vue'
import ProposalContentThreads from './ProposalContentThreads.vue'
import TopicReferencesTable from './TopicReferencesTable.vue'

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
// Content-card review notes + comments are consolidated + folded inside
// ProposalContentThreads (it filters `feedbackThreads` by anchor itself). The
// wiki is one combined doc with a `## Section` per topic; the template shows
// the selected topic's own section (`selectedDraftTopic.wiki`) and falls back
// to the full wiki (`data.wiki_preview`) when no section matched, so a topic
// pane is never blank.
const selectedDraftAliases = computed(() => selectedDraftTopic.value?.aliases || [])
const selectedProposalFailure = computed(() =>
  props.data?.selected_status || props.data?.selected_run || null
)
const totalCommentsCount = computed(() =>
  (feedbackThreads.value || []).reduce((sum, t) => sum + (t.comments?.length || 0), 0)
)
// Proposal-topic ids with an OPEN content-drift note, so the Draft Topics
// table can flag the drifted wikis among rows that otherwise all read
// `accepted`. Joined on proposal_topic_id (== a draft topic's id). Only
// `resolution_state === 'open'` counts: `addressed` is the auto-resolve sweep
// that fires on regenerate (drift already fixed), and the serializer already
// re-surfaces an addressed note as `open` when an earlier revision is selected
// — matching the run-level `orm_open_content_drift_threads` semantics.
const driftTopicIds = computed(() =>
  feedbackThreads.value
    .filter((t) => t.kind === 'content_drift'
      && t.resolution_state === 'open'
      && t.proposal_topic_id)
    .map((t) => t.proposal_topic_id)
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

async function regenerateProposal(topicIds) {
  const proposalId = selectedProposalId.value
  if (!proposalId) return
  if (!selectedRevisionIsLatest.value) {
    emit('error', 'Switch to the latest revision before regenerating.')
    return
  }
  // An explicit topic-id array narrows the redraft to those wikis; anything
  // else (e.g. the sidebar/review-note buttons) regenerates the whole run.
  const body = Array.isArray(topicIds) ? { topic_ids: topicIds } : {}
  startBusy('regenerate-proposal')
  try {
    const result = await api.post(`/repos/${props.repo}/topics/proposals/${proposalId}/regenerate`, body)
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
    parent_id: topic.parent_id || '',
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
      parent_id: proposalDraft.value.parent_id || null,
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

// Bulk "Apply All" for the Draft Topics table — extracted to a composable to
// keep this SFC's surface area down. Reuses the per-topic /apply endpoint.
const { applyAll } = useProposalApplyAll(props, {
  selectedProposalId,
  proposalReadyToApply,
  selectedRevisionIsLatest,
  askConfirm: confirm,
  startBusy,
  stopBusy,
  onError: (msg) => emit('error', msg),
  onDone: () => emit('refresh-all'),
  resetApplying: () => { applyingTopicId.value = null },
})

watch(selectedProposalId, () => {
  applyingTopicId.value = null
  editingProposalTopicId.value = null
  commentsDrawerOpen.value = false
})

</script>

<template>
  <div v-if="!selectedRun" class="topics-runs-empty">
    <p class="text-sm text-slate-600">
      Run <code class="text-xs">{{ route.query.proposal }}</code> not found.
    </p>
    <Button variant="secondary" class="mt-3" @click="backToList">← Back to runs</Button>
  </div>
  <div v-else class="topics-run-detail">
    <Card>
      <ProposalRunHeader
        :selected-run="selectedRun"
        :prev-run="prevRun"
        :next-run="nextRun"
        :is-active-run="isActiveRun"
        :selected-proposal-review-state="selectedProposalReviewState"
        :selected-revision="selectedRevision"
        :selected-revision-is-latest="selectedRevisionIsLatest"
        :selected-proposal-id="selectedProposalId"
        :regenerate-topics="data?.draft_topics || []"
        :drift-topic-ids="selectedRun?.open_drift_topics || []"
        :busy-action="busyAction"
        @back="backToList"
        @choose-run="chooseRun"
        @stop="stopProposal"
        @update-review-state="updateReviewState"
        @regenerate="regenerateProposal"
        @delete="deleteRun"
      />
    </Card>

    <ProposalDraftTopicsTable
      :draft-topics="data?.draft_topics || []"
      :selected-draft-topic-id="selectedDraftTopicId"
      :drift-topic-ids="driftTopicIds"
      :can-apply-all="proposalReadyToApply && selectedRevisionIsLatest && !applyingTopicId && !isActiveRun"
      :applying-all="isBusy('apply-all')"
      @select="chooseDraftTopic"
      @apply-all="applyAll"
    />

    <div class="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <Card>
        <div v-if="selectedDraftTopic" class="space-y-5">
          <div class="topics-detail-header">
            <div class="topics-detail-copy">
              <div class="flex items-center justify-between gap-2 flex-wrap">
                <p class="topics-detail-eyebrow">Draft Topic</p>
                <Button
                  variant="secondary"
                  size="sm"
                  class="xl:hidden"
                  @click="commentsDrawerOpen = true"
                >Comments ({{ totalCommentsCount }})</Button>
              </div>
              <div class="flex flex-wrap items-center gap-2">
                <h2 class="topics-detail-title">{{ selectedDraftTopic.label }}</h2>
                <Badge color="purple" :label="`${selectedDraftFeedbackThreadCount} thread${selectedDraftFeedbackThreadCount === 1 ? '' : 's'}`" />
              </div>
              <p class="text-sm text-gray-600">{{ selectedDraftTopic.intent }}</p>
              <p
                v-if="!proposalReadyToApply && isPendingTopic(selectedDraftTopic)"
                class="text-xs text-amber-700 mt-2"
              >
                Review is still in progress. Mark the proposal ready before applying draft topics.
              </p>
              <div v-if="selectedRevisionIsHistorical" class="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <span>You are viewing a historical revision. Editing, review-state changes, apply, and new comments are disabled here.</span>
                <Button
                  variant="secondary"
                  size="sm"
                  @click="chooseRevision(undefined)"
                >Back to latest revision</Button>
                <Button
                  variant="primary"
                  size="sm"
                  :disabled="isBusy('restore-revision')"
                  :title="`Append a new revision based on r${selectedRevision?.revision_number}`"
                  @click="restoreRevision"
                >{{ isBusy('restore-revision') ? 'Restoring…' : `Restore this revision` }}</Button>
              </div>
            </div>
            <div class="topics-detail-actions">
              <div class="topics-detail-status-row">
                <Badge :color="topicReviewStatusColor(selectedDraftTopic.review_status)" :label="selectedDraftTopic.review_status || 'pending'" />
              </div>
              <div class="topics-detail-button-row btn-row">
                <Button
                  v-if="selectedDraftTopic.review_status !== 'accepted'"
                  variant="secondary"
                  :disabled="isBusy() || !selectedRevisionIsLatest"
                  @click="editProposedTopic(selectedDraftTopic)"
                >Edit</Button>
                <Button
                  v-if="isPendingTopic(selectedDraftTopic)
                        && applyingTopicId !== selectedDraftTopic.id"
                  variant="primary"
                  :disabled="isBusy() || !proposalReadyToApply || !selectedRevisionIsLatest"
                  data-testid="apply-proposed-topic"
                  @click="openApplyPanel(selectedDraftTopic)"
                >Apply</Button>
                <Button
                  v-if="!selectedDraftTopic.review_status"
                  variant="danger"
                  :disabled="isBusy() || !selectedRevisionIsLatest"
                  @click="ignoreProposedTopic(selectedDraftTopic)"
                >Ignore</Button>
              </div>
            </div>
          </div>

          <ProposalContentThreads
            :feedback-threads="feedbackThreads"
            :selected-topic-id="selectedDraftTopicId"
            :selected-topic-label="selectedDraftTopic.label"
          />

          <div v-if="data?.revisions?.length" class="topics-candidate-card">
            <div class="flex items-center justify-between gap-2">
              <h3 class="topics-subsection-title">Revision History</h3>
              <router-link
                v-if="(data.revisions || []).length >= 2"
                class="text-xs font-medium text-blue-700 hover:text-blue-900 hover:underline"
                :to="{ name: 'repo-topics-compare', params: { name: repo }, query: { proposal: selectedProposalId } }"
                data-testid="compare-revisions-link"
              >
                Compare revisions →
              </router-link>
            </div>
            <p class="text-sm text-slate-600 mb-3">
              Browse previous revisions to compare drafts. Historical revisions are read-only.
            </p>
            <div class="flex flex-wrap gap-2">
              <Button
                v-for="revision in data.revisions"
                :key="revision.id"
                variant="secondary"
                size="sm"
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
              </Button>
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
              Bucket (taxonomy placement)
              <Select v-model="proposalDraft.parent_id" block aria-label="Draft topic bucket" class="mt-1"
                :options="[{ value: '', label: '— Unclassified (reviewer to place) —' }, ...((data && data.buckets) || []).map(b => ({ value: b.id, label: b.label }))]" />
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
              <Button variant="primary" :disabled="isBusy() || !selectedRevisionIsLatest" @click="saveProposedTopic(selectedDraftTopic)">Save</Button>
              <Button variant="secondary" :disabled="isBusy()" @click="editingProposalTopicId = null">Cancel</Button>
            </div>
          </div>

          <div v-if="!wikiLoaded" class="topics-candidate-card">
            <h3 class="topics-subsection-title">Merge Target</h3>
            <p class="text-sm text-gray-600">Merge uses the topic selected in Approved mode. Open Approved once to choose a target topic.</p>
          </div>

          <div class="space-y-2">
            <h3 class="topics-subsection-title !mb-0">Intent</h3>
            <p class="text-sm text-slate-700">{{ selectedDraftTopic.intent }}</p>
          </div>

          <div class="space-y-2">
            <h3 class="topics-subsection-title !mb-0">Aliases</h3>
            <div v-if="selectedDraftAliases.length" class="flex flex-wrap gap-2">
              <code v-for="alias in selectedDraftAliases" :key="alias" class="text-xs">{{ alias }}</code>
            </div>
            <p v-else class="text-sm text-slate-500">No aliases proposed for this topic.</p>
          </div>

          <div v-if="(selectedDraftTopic.edges || []).length" class="space-y-2">
            <h3 class="topics-subsection-title !mb-0">Related topics</h3>
            <div class="flex flex-wrap gap-2">
              <Badge
                v-for="edge in selectedDraftTopic.edges"
                :key="`${edge.type || 'related'}:${edge.target || edge.to}`"
                color="gray"
                :label="`${edge.type || 'related'}: ${edge.target || edge.to}`"
              />
            </div>
          </div>

          <TopicReferencesTable :refs="selectedDraftTopic.refs || []" />

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

          <div v-if="selectedDraftTopic?.wiki || data?.wiki_preview" class="topics-markdown">
            <h3 class="topics-subsection-title !mb-0">Wiki Preview</h3>
            <details v-if="selectedDraftTopic?.wiki && data?.wiki_intro" class="topics-wiki-intro mb-3">
              <summary class="text-xs font-medium text-slate-500">Shared proposal overview</summary>
              <MarkdownContent :markdown="data.wiki_intro" class="mt-2" />
            </details>
            <MarkdownContent :markdown="selectedDraftTopic?.wiki || data.wiki_preview" />
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
          @regenerate="regenerateProposal"
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
            <Button
              variant="secondary"
              size="sm"
              @click="commentsDrawerOpen = false"
            >Close</Button>
          </header>
          <div class="topics-comments-drawer-body">
            <ProposalCommentsSidebar
              :repo-name="repo"
              :proposal-id="selectedProposalId"
              :selected-topic="selectedDraftTopic"
              :threads="feedbackThreads"
              :readonly="!selectedRevisionIsLatest"
              @updated="emit('refresh')"
              @regenerate="regenerateProposal"
            />
          </div>
        </aside>
      </div>
    </transition>
  </div>
</template>
