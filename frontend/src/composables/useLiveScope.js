import { ref, computed, reactive } from 'vue'
import { partitionScope } from '../utils/liveRows.js'

// Per-agent span scoping for the /live card. scopeId = an agent_id → the
// tail shows only that subagent's internal spans; null = the main timeline
// (spans without agent_id + the launch/done markers). The header keeps
// showing MAIN-session truth throughout — this only re-partitions the
// tail. Scroll save/restore stays in the view (it owns the tail element).
// The scoped tail must open with the subagent's task statement. New sessions
// carry a real `prompt-sa-<agent_id>` span (agent_id-tagged, so it partitions
// into scope naturally); old sessions have none, so synthesize a leading
// prompt row from the launch `tool_input.prompt` / roster description. Only
// synthesize when real internal spans loaded but no prompt among them — an
// agent with zero captured spans must stay in its empty-scope terminal state.
function withLeadingPrompt(base, scopeId, agent) {
  if (!scopeId || !base.length || base.some(s => s.name === 'prompt')) return base
  const text = agent?.startSpan?.attributes?.prompt_preview
    || agent?.description || ''
  if (!text) return base
  return [{
    span_id: `prompt-sa-synth-${scopeId}`,
    name: 'prompt',
    parent_id: null,
    start_time: base[0].start_time,
    attributes: { text, agent_id: scopeId },
  }, ...base]
}

export function useLiveScope(getSpans, getAgents) {
  const scopeId = ref(null)
  const scopedAgent = computed(() => (scopeId.value
    ? getAgents().find(a => a.agentId === scopeId.value) || null
    : null))
  const scopedSpans = computed(() => withLeadingPrompt(
    partitionScope(getSpans(), scopeId.value), scopeId.value, scopedAgent.value))
  // The main-timeline partition regardless of the active scope: the NOW zone
  // projects over THIS, never the raw tail — a subagent's assistant_response
  // (agent_id set) must not surface as the main session's "latest response".
  const mainSpans = computed(() => partitionScope(getSpans(), null))
  // Empty-scope honesty, on the server's spanCount ALONE: it knows how many
  // internal spans the agent HAS even when none sit in the loaded window —
  // "not loaded yet" and "never captured" are different terminal states,
  // and pageable history existing (hasMoreOlder) says nothing about THIS
  // agent's spans.
  const scopedSpansExist = computed(() => (scopedAgent.value?.spanCount || 0) > 0)
  const scopedEmptyHint = computed(() => (scopedSpansExist.value
    ? 'spans not loaded — load earlier history to view'
    : 'no spans captured for this agent'))

  function enter(agentId) { if (agentId) scopeId.value = agentId }
  function exit() { scopeId.value = null }

  return reactive({
    scopeId, scopedSpans, mainSpans, scopedAgent,
    scopedSpansExist, scopedEmptyHint, enter, exit,
  })
}
