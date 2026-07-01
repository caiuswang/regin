import api from '../api'
import {
  defaultApplyOptions,
  initialApplyStrategy,
  isPendingTopic,
  proposalTopicPath,
} from '../utils/proposalApply'

// Bulk-apply every pending draft topic in a proposal.
//
// Kept out of ProposalRunDetail.vue so that already-large SFC's surface area
// (vue-complexity god-component gate) doesn't grow. Applies through the SAME
// per-topic /apply endpoint the DiffPanel uses — no new backend write path —
// so every side-effect (forward-edge staging, drift-baseline advance, status
// bookkeeping) fires exactly as it does for a manual apply.
//
// `props` is the reactive ProposalRunDetail props proxy (read for repo / data
// / approvedTopicIds). The rest are computed refs + callbacks the SFC owns.
export function useProposalApplyAll(props, {
  selectedProposalId,
  proposalReadyToApply,
  selectedRevisionIsLatest,
  askConfirm,
  startBusy,
  stopBusy,
  onError,
  onDone,
  resetApplying,
}) {
  // Strategy auto-picks replace-vs-create (shared with DiffPanel); merge needs
  // a human-chosen target so it's never auto-selected — such a topic stays
  // pending for a manual Apply.
  async function applyOneTopic(topic) {
    const url = proposalTopicPath(props.repo, selectedProposalId.value, topic.id, 'apply')
    // Swallow both a soft failure ({ok:false}) and a hard network reject so a
    // single bad apply can't abort the rest of the batch — every topic is
    // attempted and every failure is counted.
    try {
      const res = await api.post(url, {
        strategy: initialApplyStrategy(topic.id, props.approvedTopicIds),
        target_topic_id: null,
        options: defaultApplyOptions(),
      })
      return { ok: Boolean(res?.ok) }
    } catch {
      return { ok: false }
    }
  }

  // Sequential (not parallel) so each apply recomputes its diff against the
  // running graph — that's what lets a doc's edge to a not-yet-applied sibling
  // get staged and re-attached when the sibling lands. Partial failures don't
  // abort the run; they're collected and surfaced so the user can finish them.
  async function applyAll() {
    const proposalId = selectedProposalId.value
    if (!proposalId || !proposalReadyToApply.value || !selectedRevisionIsLatest.value) return
    const pending = (props.data?.draft_topics || []).filter(isPendingTopic)
    if (!pending.length) return
    const ok = await askConfirm(
      'Apply all draft topics',
      `Apply ${pending.length} pending draft topic${pending.length === 1 ? '' : 's'} from this proposal? Each is applied to the approved graph.`,
      false,
    )
    if (!ok) return
    resetApplying()
    startBusy('apply-all')
    const failed = []
    try {
      for (const topic of pending) {
        const res = await applyOneTopic(topic)
        if (!res.ok) failed.push(topic.id)
      }
    } finally {
      stopBusy()
    }
    // Refresh FIRST — onDone triggers a workspace reload whose first act is to
    // clear the shared proposal-error banner. Reporting the partial failure
    // after that reload is what lets the "could not auto-apply" message
    // actually survive on screen (otherwise the refresh wipes it same-frame).
    onDone()
    if (failed.length) {
      onError(`Applied ${pending.length - failed.length} of ${pending.length}. `
        + `Could not auto-apply: ${failed.join(', ')} — finish these with the per-topic Apply.`)
    }
  }

  return { applyAll }
}
