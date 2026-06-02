import { ref, computed } from 'vue'
import api from '../api'

// Header pivot metadata for a session: the plans it authored/edited and the
// dynamic-workflow runs it launched, plus — when the session IS a workflow
// run — its stale-snapshot marker and the backlink to the launching session.
//
// `route` supplies the session id; `allSpans` is the shared live span computed
// (the run-root `session.start` span carries the workflow attributes).
export function useWorkflowMeta(route, allSpans) {
  // Plans this session authored/edited — surfaced in the header as clickable
  // chips. Populated from `plan_sessions` rows scoped to this trace_id.
  const plans = ref([])

  // Dynamic-workflow runs this session launched (its `tool.Workflow` calls,
  // each stamped with workflow_run_id + name at ingest).
  const workflowRuns = ref([])

  // run_id → enriched run record (agent_count, phase_count, status, tokens),
  // so the inline `tool.Workflow` spine row and the detail panel can render a
  // rich collapsed summary without each opening the run's trace.
  const workflowRunsById = computed(() => {
    const map = {}
    for (const r of workflowRuns.value) map[r.run_id] = r
    return map
  })

  // The run-root session.start span for a dynamic-workflow run (null for
  // ordinary sessions). Source of the parent backlink + stale-snapshot marker.
  const workflowRoot = computed(() => allSpans.value.find(
    s => s.name === 'session.start' && s.attributes?.run_id != null,
  ) || null)

  // Set when the rendered tree is a *stale* manifest snapshot: the run has
  // resumed and progressed past the snapshot, but the runtime only flushes the
  // manifest at pause/completion, so phases/counts here lag reality. Value is
  // the ISO time the snapshot was taken.
  const snapshotStaleAt = computed(
    () => workflowRoot.value?.attributes?.snapshot_stale_at || null)

  // Backlink target: the Claude Code session (and exact tool.Workflow span)
  // this run was launched from. `parent_trace_id` is stamped on the run root
  // at ingest; `parent_span_id` is added once the launching tool call is
  // matched, so the chip deep-links straight to that call when available.
  const workflowParent = computed(() => {
    const a = workflowRoot.value?.attributes
    if (!a?.parent_trace_id) return null
    return { traceId: a.parent_trace_id, spanId: a.parent_span_id || null }
  })
  const workflowParentTo = computed(() => {
    const p = workflowParent.value
    if (!p) return null
    return p.spanId
      ? { path: `/trace/sessions/${p.traceId}`, query: { span: p.spanId } }
      : { path: `/trace/sessions/${p.traceId}` }
  })

  async function fetchPlans() {
    try {
      const data = await api.get(
        `/plan-sessions?session=${encodeURIComponent(route.params.id)}&size=20`
      )
      plans.value = data.items || []
    } catch (e) {
      plans.value = []
    }
  }

  async function fetchWorkflowRuns() {
    try {
      const data = await api.get(
        `/sessions/${encodeURIComponent(route.params.id)}/workflow-runs`
      )
      workflowRuns.value = data.items || []
    } catch (e) {
      workflowRuns.value = []
    }
  }

  return {
    plans, workflowRuns, workflowRunsById,
    snapshotStaleAt, workflowParentTo,
    fetchPlans, fetchWorkflowRuns,
  }
}
