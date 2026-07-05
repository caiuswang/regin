import { ref, computed, reactive } from 'vue'
import { partitionScope } from '../utils/liveRows.js'

// Per-agent span scoping for the /live card. scopeId = an agent_id → the
// tail shows only that subagent's internal spans; null = the main timeline
// (spans without agent_id + the launch/done markers). The header keeps
// showing MAIN-session truth throughout — this only re-partitions the
// tail. Scroll save/restore stays in the view (it owns the tail element).
export function useLiveScope(getSpans, getAgents) {
  const scopeId = ref(null)
  const scopedSpans = computed(() => partitionScope(getSpans(), scopeId.value))
  // The main-timeline partition regardless of the active scope: the NOW zone
  // projects over THIS, never the raw tail — a subagent's assistant_response
  // (agent_id set) must not surface as the main session's "latest response".
  const mainSpans = computed(() => partitionScope(getSpans(), null))
  const scopedAgent = computed(() => (scopeId.value
    ? getAgents().find(a => a.agentId === scopeId.value) || null
    : null))
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
