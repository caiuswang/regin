import { ref, computed, reactive } from 'vue'
import { partitionScope, SCOPE_MARKERS } from '../utils/liveRows.js'
import { useAgentTrace } from './useAgentTrace.js'

// Per-agent span scoping for the /live card. scopeId = an agent_id → the
// tail shows that subagent's OWN trace (useAgentTrace); null = the main
// timeline (spans without agent_id + the launch/done markers). The header
// keeps showing MAIN-session truth throughout — this only re-projects the
// tail. Scroll save/restore stays in the view (it owns the tail element).
// The scoped tail must open with the subagent's task statement. New sessions
// carry a real `prompt-sa-<agent_id>` span (agent_id-tagged, so it partitions
// into scope naturally); old sessions have none, so synthesize a leading
// prompt row from the launch `tool_input.prompt` / roster description. Only
// synthesize when real internal spans loaded but no prompt among them — an
// agent with zero captured spans must stay in its empty-scope terminal state.
function withLeadingPrompt(base, scopeId, agent) {
  if (!scopeId || !base.length || base.some(s => s.name === 'prompt')) return base
  const marker = agent?.startSpan?.attributes || {}
  const text = marker.prompt_preview || marker.prompt
    || agent?.promptPreview || agent?.description || ''
  if (!text) return base
  return [{
    span_id: `prompt-sa-synth-${scopeId}`,
    name: 'prompt',
    parent_id: null,
    start_time: base[0].start_time,
    attributes: { text, agent_id: scopeId },
  }, ...base]
}

// Workflow-run markers carry the agent's result inline and never get a
// subagent.stop span (partitionScope excludes markers from scopes anyway) —
// close the scoped tail with a synthesized "finished" row so the result is
// readable where the work ends. Synthetic id, never fetched: the /live sheet
// renders from the local span object.
function withTrailingResult(base, scopeId, agent) {
  if (!scopeId || !base.length) return base
  const marker = agent?.startSpan?.attributes || {}
  if (!marker.result_preview && !marker.result_full) return base
  return [...base, {
    span_id: `result-sa-synth-${scopeId}`,
    name: 'subagent.stop',
    parent_id: null,
    start_time: base[base.length - 1].start_time,
    attributes: {
      agent_id: scopeId,
      agent_type: marker.agent_type || '',
      label: marker.label || '',
      result_preview: marker.result_preview || marker.result_full,
    },
  }]
}

export function useLiveScope(getSpans, getAgents) {
  const scopeId = ref(null)
  const scopedAgent = computed(() => (scopeId.value
    ? getAgents().find(a => a.agentId === scopeId.value) || null
    : null))
  // The tail's own pages can't be partitioned into a scope any more: a
  // subagent's spans are served by ITS trace, not the parent's projection.
  const agentTrace = useAgentTrace(() => scopeId.value)
  // The main-timeline partition regardless of the active scope: the NOW zone
  // projects over THIS, never the raw tail — a subagent's assistant_response
  // (agent_id set) must not surface as the main session's "latest response".
  const mainSpans = computed(() => partitionScope(getSpans(), null))
  // What the tail renders: the agent's own trace in a scope, the main
  // partition out of one.
  const scopedSpans = computed(() => (scopeId.value
    ? withTrailingResult(withLeadingPrompt(
      // The agent's own trace serves its markers as well as its work, and a
      // marker belongs to MAIN's view of the agent, never the scope — an
      // agent that captured nothing must still read as empty here.
      agentTrace.spans.filter(s => !SCOPE_MARKERS.has(s.name)),
      scopeId.value, scopedAgent.value,
    ), scopeId.value, scopedAgent.value)
    : mainSpans.value))
  // Empty-scope honesty, on the server's spanCount ALONE: it knows how many
  // internal spans the agent HAS even when none sit in the loaded window —
  // "not loaded yet" and "never captured" are different terminal states,
  // and pageable history existing (hasMoreOlder) says nothing about THIS
  // agent's spans.
  const scopedSpansExist = computed(() => (scopedAgent.value?.spanCount || 0) > 0)
  const scopedEmptyHint = computed(() => (scopedSpansExist.value
    ? 'spans not loaded — load earlier history to view'
    : 'no spans captured for this agent'))

  // The child-trace fetch in flight — distinct from the terminal empty
  // states above, so the view renders "loading…" instead of prematurely
  // flashing scopedEmptyHint.
  const scopeLoading = computed(() => agentTrace.loading)

  function enter(agentId) { if (agentId) scopeId.value = agentId }
  function exit() { scopeId.value = null }

  return reactive({
    scopeId, scopedSpans, mainSpans, scopedAgent,
    scopedSpansExist, scopedEmptyHint, scopeLoading, enter, exit,
  })
}
