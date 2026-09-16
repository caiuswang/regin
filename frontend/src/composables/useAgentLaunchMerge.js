import { computed } from 'vue'

// ── Subagent launch merge ─────────────────────────────────────
// `tool.Agent` (the launch — carries description + prompt) and
// `subagent.start` (the run) are separate spans. Fold the launch into the
// subagent row: by the launching call's id where the marker records it
// (`launch_tool_use_id`), else by same parent prompt + matching
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
    const starts = spans.filter(s => s.name === 'subagent.start')
    const claimed = new Set()
    function bestLaunch(start, requireParent) {
      const aType = start.attributes?.agent_type || ''
      let best = null
      let bestDt = Infinity
      for (const lc of launches) {
        if (claimed.has(lc.span_id)) continue
        if (requireParent && (lc.parent_id || null) !== (start.parent_id || null)) continue
        if ((lc.attributes?.subagent_type || '') !== aType) continue
        const dt = Math.abs(new Date(lc.start_time) - new Date(start.start_time))
        if (dt < bestDt) { bestDt = dt; best = lc }
      }
      return best
    }
    function pair(start, launch) {
      byStart.set(start.span_id, launch)
      merged.add(launch.span_id)
      claimed.add(launch.span_id)
    }
    function pairStarts(requireParent) {
      for (const start of starts) {
        if (byStart.has(start.span_id)) continue
        const best = bestLaunch(start, requireParent)
        if (best) pair(start, best)
      }
    }
    // The marker records the id of the call that launched it, so the pair is
    // known outright wherever both spans are in the window — the passes below
    // are for the spans that predate the link. Still one launch per start: a
    // resumed agent's runs all name the ONE launch, and only its first run was
    // really asked that (the later ones carry `agent_segment_prompt`).
    const byToolUse = new Map()
    for (const lc of launches) {
      const id = lc.tool_use_id || lc.attributes?.tool_use_id
      if (id && !byToolUse.has(id)) byToolUse.set(id, lc)
    }
    for (const start of starts) {
      const launch = byToolUse.get(start.attributes?.launch_tool_use_id)
      if (launch && !claimed.has(launch.span_id)) pair(start, launch)
    }
    // Two passes: same-parent pairs first (the strong signal), then a
    // parent-blind sweep for the rest. Live, the two spans often sit on
    // different branches until the next reconcile — the launch gets
    // turn-attributed to its think-/resp- anchor while subagent.start is
    // still grafted at the prompt root — so requiring equal parents would
    // leave the running agent's row without its goal.
    pairStarts(true)
    pairStarts(false)
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
  // `agent_launch_*` is the serve-time copy of a launch the feed never sees:
  // an agent launched BY another agent has its `tool.Agent` among that agent's
  // delegated work, withheld from this trace (`projection.drop_delegated_spans`).
  function agentDescription(span) {
    return launchForSubagent(span)?.attributes?.description
      || span.attributes?.agent_launch_description
      || span.attributes?.label || ''
  }
  // A resumed agent's later run has its own prompt (the SendMessage turn, read
  // off the agent's transcript) and no launch of its own — the single
  // `tool.Agent` is claimed by the first run. Its own turn outranks both.
  function agentPrompt(span) {
    return span.attributes?.agent_segment_prompt
      || launchForSubagent(span)?.attributes?.prompt
      || span.attributes?.agent_launch_prompt
      || span.attributes?.prompt || ''
  }
  function agentPromptOwnerId(span) {
    return launchForSubagent(span)?.span_id || span.span_id
  }
  // Number of agents under a workflow.phase, for the phase-divider label.
  function agentCountForPhase(spanId) {
    return childrenOf(spanId).filter((s) => s.name === 'subagent.start').length
  }
  // `agent_result_preview` is the serve-time copy of the agent's own
  // `subagent.stop` result — an ordinary Task subagent writes its answer
  // nowhere the start marker can see, so without it its card has no result to
  // show. Ranked below the segment key, which describes ONE run of a resumed
  // agent where this one describes the whole of a single-run agent.
  function agentResultText(span) {
    return span.attributes?.agent_segment_result
      || span.attributes?.result_full || span.attributes?.result_preview
      || span.attributes?.agent_result_preview || ''
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
  }
}
