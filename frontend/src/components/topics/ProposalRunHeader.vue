<script setup>
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import { fmtAgo } from '../../utils/traceFormatters'

const props = defineProps({
  selectedRun: { type: Object, default: null },
  prevRun: { type: Object, default: null },
  nextRun: { type: Object, default: null },
  isActiveRun: { type: Boolean, default: false },
  selectedProposalReviewState: { type: String, default: 'draft' },
  selectedRevision: { type: Object, default: null },
  selectedRevisionIsLatest: { type: Boolean, default: true },
  selectedProposalId: { type: String, default: '' },
  busyAction: { type: String, default: '' },
})

const emit = defineEmits([
  'back',
  'choose-run',
  'stop',
  'update-review-state',
  'regenerate',
  'delete',
])

function isBusy(action = '') {
  if (!props.busyAction) return false
  return action ? props.busyAction === action : true
}

function proposalStateColor(state) {
  if (state === 'completed') return 'green'
  if (state === 'failed' || state === 'timed_out') return 'red'
  if (state === 'waiting_for_permission') return 'yellow'
  if (state === 'running' || state === 'queued') return 'blue'
  if (state === 'cancelled') return 'gray'
  return 'gray'
}

function proposalReviewColor(status) {
  if (status === 'ready_to_apply') return 'green'
  if (status === 'changes_requested') return 'yellow'
  if (status === 'partially_applied') return 'blue'
  if (status === 'applied') return 'green'
  return 'gray'
}
</script>

<template>
  <div class="topics-run-header-strip">
    <div class="topics-run-header-left">
      <Button
        variant="link"
        class="topics-back-link"
        @click="emit('back')"
      >← Back to runs</Button>
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
      <Button
        variant="secondary"
        size="sm"
        :disabled="!prevRun"
        :title="prevRun ? `Previous: ${prevRun.id}` : 'No earlier run'"
        @click="emit('choose-run', prevRun?.id)"
      >← Prev</Button>
      <Button
        variant="secondary"
        size="sm"
        :disabled="!nextRun"
        :title="nextRun ? `Next: ${nextRun.id}` : 'No later run'"
        @click="emit('choose-run', nextRun?.id)"
      >Next →</Button>
      <span class="topics-run-header-divider" aria-hidden="true"></span>
      <Button
        v-if="isActiveRun"
        variant="danger"
        :disabled="isBusy()"
        title="Terminate the running agent and cancel this run"
        @click="emit('stop')"
      >{{ isBusy('stop-proposal') ? 'Stopping…' : 'Stop' }}</Button>
      <Button
        v-if="!['applied', 'partially_applied'].includes(selectedProposalReviewState)"
        variant="secondary"
        :disabled="isBusy() || !selectedRevisionIsLatest"
        @click="emit('update-review-state', 'changes_requested')"
      >Request changes</Button>
      <Button
        v-if="!['ready_to_apply', 'partially_applied', 'applied'].includes(selectedProposalReviewState)"
        variant="primary"
        :disabled="isBusy() || !selectedRevisionIsLatest"
        @click="emit('update-review-state', 'ready_to_apply')"
      >Mark ready</Button>
      <Button
        variant="secondary"
        :disabled="isBusy() || !selectedProposalId || !selectedRevisionIsLatest"
        @click="emit('regenerate')"
      >Regenerate</Button>
      <Button
        variant="danger"
        :disabled="isBusy()"
        @click="emit('delete')"
      >Delete Run</Button>
    </div>
  </div>
</template>
