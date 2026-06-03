import { ref, computed } from 'vue'

// All expand/collapse state for the conversation spine, in one place. Each
// concern is a Set of span_ids plus is*/toggle* helpers; the orchestrator and
// the leaf cards drive them through the returned object.
//
// Two callbacks keep this decoupled from the content/pins concerns:
//   onLoadSubtree(spanId)  — lazily pull a span's collapsed subtree
//   onExpandPrompt(spanId) — load-subtree + fetch missing content for a prompt
// (passed in from the orchestrator, which owns the emit + content fetch).
export function useConversationFolding({ getSpans, childrenOf, onLoadSubtree, onExpandPrompt }) {
  // Prompts are expanded by default — the conversation tab reads like a
  // continuous document; the auto-expand watcher (orchestrator) seeds this.
  const expandedPromptIds = ref(new Set())
  const expandedPromptBodyIds = ref(new Set())
  // Bash spans collapse their stdout/stderr by default; Edit/Write/MultiEdit
  // diffs and ToolSearch attribute cards do the same — keep the spine scannable.
  const expandedBashIds = ref(new Set())
  const expandedDiffIds = ref(new Set())
  const expandedToolSearchIds = ref(new Set())
  // Every subagent folds its whole subtree by default; same for a
  // `tool.Workflow` launch (its re-parented `subagent.start` markers).
  const expandedAgents = ref(new Set())
  const expandedWorkflows = ref(new Set())
  // Per-subagent prompt / result card expand state (separate sets so the two
  // collapse independently on the same span).
  const expandedAgentPrompts = ref(new Set())
  const expandedAgentResults = ref(new Set())

  // ── Agents ──────────────────────────────────────────────────
  function isAgentExpanded(spanId) { return expandedAgents.value.has(spanId) }
  function toggleAgentExpanded(spanId) {
    const next = new Set(expandedAgents.value)
    if (next.has(spanId)) next.delete(spanId)
    else { next.add(spanId); onLoadSubtree(spanId) }
    expandedAgents.value = next
  }
  // Only agents that actually have captured descendants are foldable; many
  // session `tool.Agent` launches record just the header with no child spans.
  function agentHasChildren(spanId) { return childrenOf(spanId).length > 0 }
  const foldableAgentIds = computed(() =>
    (getSpans() || [])
      .filter((s) => s.name === 'subagent.start' && agentHasChildren(s.span_id))
      .map((s) => s.span_id),
  )
  const allAgentsExpanded = computed(() =>
    foldableAgentIds.value.length > 0
    && foldableAgentIds.value.every((id) => expandedAgents.value.has(id)))
  function expandAllAgents() {
    const next = new Set(expandedAgents.value)
    for (const id of foldableAgentIds.value) {
      if (!next.has(id)) { next.add(id); onLoadSubtree(id) }
    }
    expandedAgents.value = next
  }
  function collapseAllAgents() { expandedAgents.value = new Set() }

  // ── Workflows ───────────────────────────────────────────────
  function isWorkflowExpanded(spanId) { return expandedWorkflows.value.has(spanId) }
  function toggleWorkflowExpanded(spanId) {
    const next = new Set(expandedWorkflows.value)
    if (next.has(spanId)) next.delete(spanId)
    else { next.add(spanId); onLoadSubtree(spanId) }
    expandedWorkflows.value = next
  }
  // Number of re-parented `subagent.start` markers under a `tool.Workflow` span.
  function workflowAgentCount(spanId) {
    return childrenOf(spanId).filter((s) => s.name === 'subagent.start').length
  }

  // ── Prompts ─────────────────────────────────────────────────
  function isPromptExpanded(spanId) { return expandedPromptIds.value.has(spanId) }
  function isPromptBodyExpanded(spanId) { return expandedPromptBodyIds.value.has(spanId) }
  function togglePromptExpanded(spanId, forceOpen = false) {
    if (forceOpen) {
      if (!expandedPromptIds.value.has(spanId)) {
        expandedPromptIds.value.add(spanId)
        onExpandPrompt(spanId)
      }
      return
    }
    if (expandedPromptIds.value.has(spanId)) {
      expandedPromptIds.value.delete(spanId)
      expandedPromptBodyIds.value.delete(spanId)
    } else {
      expandedPromptIds.value.add(spanId)
      onExpandPrompt(spanId)
    }
  }
  function togglePromptBodyExpanded(spanId) {
    if (expandedPromptBodyIds.value.has(spanId)) expandedPromptBodyIds.value.delete(spanId)
    else expandedPromptBodyIds.value.add(spanId)
  }

  // ── Bash / diff / tool-search output panels ─────────────────
  function bashExpanded(spanId) { return expandedBashIds.value.has(spanId) }
  function toggleBashExpanded(spanId) {
    if (expandedBashIds.value.has(spanId)) expandedBashIds.value.delete(spanId)
    else expandedBashIds.value.add(spanId)
  }
  function diffExpanded(spanId) { return expandedDiffIds.value.has(spanId) }
  function toggleDiffExpanded(spanId) {
    if (expandedDiffIds.value.has(spanId)) expandedDiffIds.value.delete(spanId)
    else expandedDiffIds.value.add(spanId)
  }
  function toolSearchExpanded(spanId) { return expandedToolSearchIds.value.has(spanId) }
  function toggleToolSearchExpanded(spanId) {
    if (expandedToolSearchIds.value.has(spanId)) expandedToolSearchIds.value.delete(spanId)
    else expandedToolSearchIds.value.add(spanId)
  }

  // ── Per-agent prompt / result cards ─────────────────────────
  function isAgentPromptExpanded(id) { return expandedAgentPrompts.value.has(id) }
  function toggleAgentPrompt(id) {
    const next = new Set(expandedAgentPrompts.value)
    if (next.has(id)) next.delete(id); else next.add(id)
    expandedAgentPrompts.value = next
  }
  function isAgentResultExpanded(id) { return expandedAgentResults.value.has(id) }
  function toggleAgentResult(id) {
    const next = new Set(expandedAgentResults.value)
    if (next.has(id)) next.delete(id); else next.add(id)
    expandedAgentResults.value = next
  }

  return {
    expandedPromptIds,
    isAgentExpanded, toggleAgentExpanded, agentHasChildren,
    foldableAgentIds, allAgentsExpanded, expandAllAgents, collapseAllAgents,
    isWorkflowExpanded, toggleWorkflowExpanded, workflowAgentCount,
    isPromptExpanded, isPromptBodyExpanded, togglePromptExpanded, togglePromptBodyExpanded,
    bashExpanded, toggleBashExpanded,
    diffExpanded, toggleDiffExpanded,
    toolSearchExpanded, toggleToolSearchExpanded,
    isAgentPromptExpanded, toggleAgentPrompt,
    isAgentResultExpanded, toggleAgentResult,
  }
}
