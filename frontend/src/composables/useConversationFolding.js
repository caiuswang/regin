import { ref } from 'vue'

// All expand/collapse state for the conversation spine, in one place. Each
// concern is a Set of span_ids plus is*/toggle* helpers; the orchestrator and
// the leaf cards drive them through the returned object.
//
// Two callbacks keep this decoupled from the content/pins concerns:
//   onLoadSubtree(spanId)  — lazily pull a span's collapsed subtree
//   onExpandPrompt(spanId) — load-subtree + fetch missing content for a prompt
// (passed in from the orchestrator, which owns the emit + content fetch).
export function useConversationFolding({ childrenOf, onLoadSubtree, onExpandPrompt }) {
  // Prompts are expanded by default — the conversation tab reads like a
  // continuous document; the auto-expand watcher (orchestrator) seeds this.
  const expandedPromptIds = ref(new Set())
  // Turns the reader closed by hand. A second set rather than the absence of
  // the first, because a turn can be open for two different reasons — the
  // reader opened it, or the detail mode / a running span opened it for them —
  // and only an explicit collapse closes the second kind. With one set, folding
  // a turn that was open by default was a silent no-op.
  const collapsedPromptIds = ref(new Set())
  const expandedPromptBodyIds = ref(new Set())
  // Bash spans collapse their stdout/stderr by default; Edit/Write/MultiEdit
  // diffs and ToolSearch attribute cards do the same — keep the spine scannable.
  const expandedBashIds = ref(new Set())
  const expandedDiffIds = ref(new Set())
  const expandedToolSearchIds = ref(new Set())
  // A `tool.Workflow` launch folds its re-parented `subagent.start` markers.
  const expandedWorkflows = ref(new Set())
  // A `/rewind` marker hides its discarded branch by default — the abandoned
  // prompt + work collapse behind the rose divider; expanding pulls the subtree.
  const expandedRewinds = ref(new Set())
  // Per-subagent prompt / result card expand state (separate sets so the two
  // collapse independently on the same span).
  const expandedAgentPrompts = ref(new Set())
  const expandedAgentResults = ref(new Set())

  // ── Rewind markers ──────────────────────────────────────────
  function isRewindExpanded(spanId) { return expandedRewinds.value.has(spanId) }
  function toggleRewindExpanded(spanId) {
    const next = new Set(expandedRewinds.value)
    if (next.has(spanId)) next.delete(spanId)
    else { next.add(spanId); onLoadSubtree(spanId) }
    expandedRewinds.value = next
  }

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
  function isPromptCollapsed(spanId) { return collapsedPromptIds.value.has(spanId) }
  function isPromptBodyExpanded(spanId) { return expandedPromptBodyIds.value.has(spanId) }
  // `open` is the state the caller WANTS, not a flip of set membership: a turn
  // the detail mode opened by default is not in `expandedPromptIds`, so a flip
  // read off that set would re-open it instead of closing it. Only the caller
  // knows what is on screen.
  function setPromptExpanded(spanId, open) {
    if (open) {
      collapsedPromptIds.value.delete(spanId)
      if (!expandedPromptIds.value.has(spanId)) {
        expandedPromptIds.value.add(spanId)
        onExpandPrompt(spanId)
      }
      return
    }
    collapsedPromptIds.value.add(spanId)
    expandedPromptIds.value.delete(spanId)
    expandedPromptBodyIds.value.delete(spanId)
  }
  function togglePromptExpanded(spanId, forceOpen = false) {
    setPromptExpanded(spanId, forceOpen || !expandedPromptIds.value.has(spanId))
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
    expandedPromptIds, collapsedPromptIds,
    isRewindExpanded, toggleRewindExpanded,
    isWorkflowExpanded, toggleWorkflowExpanded, workflowAgentCount,
    isPromptExpanded, isPromptCollapsed, isPromptBodyExpanded,
    setPromptExpanded, togglePromptExpanded, togglePromptBodyExpanded,
    bashExpanded, toggleBashExpanded,
    diffExpanded, toggleDiffExpanded,
    toolSearchExpanded, toggleToolSearchExpanded,
    isAgentPromptExpanded, toggleAgentPrompt,
    isAgentResultExpanded, toggleAgentResult,
  }
}
