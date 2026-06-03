import { computed } from 'vue'

// ── Subagent launch merge ─────────────────────────────────────
// `tool.Agent` (the launch — carries description + prompt) and
// `subagent.start` (the run) are separate spans sharing no id. Fold the
// launch into the subagent row: pair by same parent prompt + matching
// subagent_type/agent_type + nearest start_time. Unpaired launches keep
// their normal marker rendering.
//
// Also exposes the agent-metadata helpers the subagent / agent-result /
// workflow-phase cards read, with a fallback for workflow agents (which have
// NO `tool.Agent` launch span — the dispatched prompt / description / result
// live on the `subagent.start` span's own attributes instead).
export const AGENT_PROMPT_PREVIEW_CHARS = 140
export const AGENT_RESULT_PREVIEW_CHARS = 280

export function useAgentLaunchMerge(getSpans, childrenOf) {
  const agentLaunchMerge = computed(() => {
    const byStart = new Map()   // subagent.start span_id -> tool.Agent span
    const merged = new Set()    // tool.Agent span_ids folded into a subagent row
    const spans = getSpans() || []
    const launches = spans.filter(s => s.name === 'tool.Agent')
    const claimed = new Set()
    for (const start of spans.filter(s => s.name === 'subagent.start')) {
      const aType = start.attributes?.agent_type || ''
      let best = null
      let bestDt = Infinity
      for (const lc of launches) {
        if (claimed.has(lc.span_id)) continue
        if ((lc.parent_id || null) !== (start.parent_id || null)) continue
        if ((lc.attributes?.subagent_type || '') !== aType) continue
        const dt = Math.abs(new Date(lc.start_time) - new Date(start.start_time))
        if (dt < bestDt) { bestDt = dt; best = lc }
      }
      if (best) {
        byStart.set(start.span_id, best)
        merged.add(best.span_id)
        claimed.add(best.span_id)
      }
    }
    return { byStart, merged }
  })

  function launchForSubagent(startSpan) {
    return agentLaunchMerge.value.byStart.get(startSpan.span_id) || null
  }
  function agentPromptPreview(text) {
    if (!text) return ''
    return text.length > AGENT_PROMPT_PREVIEW_CHARS
      ? text.slice(0, AGENT_PROMPT_PREVIEW_CHARS).trimEnd() + '…'
      : text
  }
  function agentDescription(span) {
    return launchForSubagent(span)?.attributes?.description || span.attributes?.label || ''
  }
  function agentPrompt(span) {
    return launchForSubagent(span)?.attributes?.prompt || span.attributes?.prompt || ''
  }
  function agentPromptOwnerId(span) {
    return launchForSubagent(span)?.span_id || span.span_id
  }
  // Number of agents under a workflow.phase, for the phase-divider label.
  function agentCountForPhase(spanId) {
    return childrenOf(spanId).filter((s) => s.name === 'subagent.start').length
  }
  function agentResultText(span) {
    return span.attributes?.result_full || span.attributes?.result_preview || ''
  }
  function agentResultPreview(text) {
    if (!text) return ''
    return text.length > AGENT_RESULT_PREVIEW_CHARS
      ? text.slice(0, AGENT_RESULT_PREVIEW_CHARS).trimEnd() + '…'
      : text
  }

  return {
    agentLaunchMerge,
    launchForSubagent,
    agentPromptPreview,
    agentDescription,
    agentPrompt,
    agentPromptOwnerId,
    agentCountForPhase,
    agentResultText,
    agentResultPreview,
  }
}
