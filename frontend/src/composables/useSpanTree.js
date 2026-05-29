import { computed, unref } from 'vue'

// The conversation spine only knows how to render a small, closed set of
// root span names: `prompt` (→ turn group), `compact.pre`/`compact.post`
// (→ boundary divider), and the legacy `task.notification` standalone
// card. Every other root-level span is harness/session infrastructure
// (`session.start`, `session.end`, `harness.*`, the `conversation`
// envelope, stray `turn`/`subagent.*`) that the spine has no card for —
// it would fall through to the generic "dot + time + name" catch-all.
//
// These never reach the spine on the initial paginated shallow load
// (that returns prompt-anchored roots only), but the full span map the
// Terminal tab fetches merges them into the shared span list. An
// allowlist — rather than a denylist of known-noisy names — keeps the
// policy matched to the spine's actual render coverage, so any future
// harness event type is dropped automatically.
const CONVERSATIONAL_ROOT_NAMES = new Set([
  'prompt',
  'compact.pre',
  'compact.post',
  'task.notification',
])

/**
 * useSpanTree — span-list → tree derivations for the conversation view.
 *
 * Given a reactive list of flat spans (and optionally an aligned `turns`
 * list from the backend), this composable surfaces the lookup maps,
 * root list, recursive descendants, prompt groups, and turn items that
 * the conversation spine and TOC consume.
 *
 * All inputs can be plain refs, getters, or unwrapped values — they're
 * read via `unref()` so callers can pass `() => props.spans` from a
 * `<script setup>` block without an extra ref hop.
 *
 * Extracted from SessionConversationView (PR 2.3b). Pre-extraction the
 * view re-derived these inline; the composable lets future trace views
 * (e.g. SessionTraceView) share the same tree without copy-paste drift.
 *
 * @param {Ref<Array>|() => Array|Array} spansInput - flat spans list
 * @param {Ref<Array>|() => Array|Array} [turnsInput=null] - aligned turns[]
 * @returns {{
 *   spanById: ComputedRef<Map>,
 *   childrenByParent: ComputedRef<Map>,
 *   rootSpans: ComputedRef<Array>,
 *   childrenOf: (spanId: string) => Array,
 *   flattenDescendants: (spanId: string, depth?: number) => Array,
 *   entries: ComputedRef<Array>,
 *   promptGroups: ComputedRef<Array>,
 *   turnItems: ComputedRef<Array>,
 * }}
 */
export function useSpanTree(spansInput, turnsInput = null) {
  const spans = () =>
    typeof spansInput === 'function' ? spansInput() : (unref(spansInput) || [])
  const turns = () =>
    typeof turnsInput === 'function' ? turnsInput() : unref(turnsInput)

  const spanById = computed(() => {
    const map = new Map()
    for (const s of spans()) map.set(s.span_id, s)
    return map
  })

  const childrenByParent = computed(() => {
    const map = new Map()
    for (const s of spans()) {
      const pid = s.parent_id
      if (!pid) continue
      if (!map.has(pid)) map.set(pid, [])
      map.get(pid).push(s)
    }
    for (const [, list] of map) {
      list.sort((a, b) => {
        const at = a.start_time ? new Date(a.start_time).getTime() : 0
        const bt = b.start_time ? new Date(b.start_time).getTime() : 0
        return at - bt
      })
    }
    return map
  })

  const rootSpans = computed(() => {
    const roots = spans().filter((s) => {
      if (!CONVERSATIONAL_ROOT_NAMES.has(s.name)) return false
      if (!s.parent_id) return true
      return !spanById.value.has(s.parent_id)
    })
    return roots.sort((a, b) => {
      const at = a.start_time ? new Date(a.start_time).getTime() : 0
      const bt = b.start_time ? new Date(b.start_time).getTime() : 0
      return at - bt
    })
  })

  function childrenOf(spanId) {
    return childrenByParent.value.get(spanId) || []
  }

  // `inAgent` marks every row that lives inside a subagent's subtree (anything
  // below a `subagent.start`), so the conversation spine can draw a grouping
  // rail and the reader can tell a subagent's spans apart from the main thread.
  // Once set it propagates to all deeper descendants (incl. nested subagents).
  function flattenDescendants(spanId, depth = 0, inAgent = false) {
    const children = childrenOf(spanId)
    const result = []
    for (const child of children) {
      result.push({ span: child, depth, inAgent })
      const grandchildren = childrenOf(child.span_id)
      if (grandchildren.length) {
        const childInAgent = inAgent || child.name === 'subagent.start'
        result.push(...flattenDescendants(child.span_id, depth + 1, childInAgent))
      }
    }
    return result
  }

  // ── Dynamic-workflow projection ───────────────────────────────
  // A workflow run is captured as one trace: a `session.start` root identified
  // by its `run_id`, a `prompt` objective, `workflow.phase` bands, and
  // `subagent.start` agents carrying their assistant_response / tool.* turns.
  // The normal prompt-anchored spine renders nothing (the objective prompt
  // has no descendants — phases hang off the run root), so we project the
  // run into a single conversation group: the objective as the opening USER
  // card, then each phase as a divider row followed by its agents + turns.
  const workflowRoot = computed(() => {
    for (const s of spans()) {
      if (s.name === 'session.start' && s.attributes?.run_id != null) return s
    }
    const phase = spans().find((s) => s.name === 'workflow.phase')
    if (phase?.parent_id) return spanById.value.get(phase.parent_id) || null
    return null
  })

  const isWorkflow = computed(() =>
    !!workflowRoot.value || spans().some((s) => s.name === 'workflow.phase'),
  )

  function _orderedPhases() {
    return spans()
      .filter((s) => s.name === 'workflow.phase')
      .sort((a, b) => (a.attributes?.index ?? 1e9) - (b.attributes?.index ?? 1e9))
  }

  // Project one agent into the flat descendant list as: its header row, then
  // its turns (tagged `inAgent` so the spine can draw a grouping rail), then a
  // synthetic `workflow.agent_result` marker. Deferring the result to the end
  // makes each agent read prompt → work → result — the result card used to be
  // bundled into the header block and render BEFORE the agent's turns.
  function _pushAgent(out, agent, depth) {
    out.push({ span: agent, depth })
    out.push(...flattenDescendants(agent.span_id, depth + 1).map((d) => ({ ...d, inAgent: true })))
    if (agent.attributes?.result_full || agent.attributes?.result_preview) {
      out.push({
        span: { ...agent, span_id: `${agent.span_id}::result`, name: 'workflow.agent_result' },
        depth: depth + 1,
        inAgent: true,
      })
    }
  }

  function _workflowDescendants(rootId, phases) {
    const out = []
    if (phases.length) {
      for (const phase of phases) {
        out.push({ span: phase, depth: 0 })
        for (const agent of childrenOf(phase.span_id)) {
          if (agent.name !== 'subagent.start') continue
          _pushAgent(out, agent, 1)
        }
      }
    } else if (rootId) {
      // Live/flat run: no phases yet — agents hang directly off the root.
      for (const agent of childrenOf(rootId)) {
        if (agent.name !== 'subagent.start') continue
        _pushAgent(out, agent, 0)
      }
    }
    return out
  }

  function workflowEntries() {
    const root = workflowRoot.value
    const rootId = root?.span_id || null
    const objective =
      spans().find((s) => s.name === 'prompt' && s.parent_id === rootId) ||
      spans().find((s) => s.name === 'prompt') || null
    const descendants = _workflowDescendants(rootId, _orderedPhases())
    if (!objective && !descendants.length) return []
    return [{ type: 'group', prompt: objective || root, descendants, workflow: true }]
  }

  const entries = computed(() => {
    if (isWorkflow.value) return workflowEntries()
    const out = []
    for (const root of rootSpans.value) {
      if (root.name === 'prompt') {
        out.push({
          type: 'group',
          prompt: root,
          descendants: flattenDescendants(root.span_id),
        })
      } else {
        // Background-task notifications used to anchor their own TURN
        // row, but they're now nested under the preceding `prompt` by
        // `_graft_orphans` — so they only reach this branch in legacy
        // sessions, where we render them inline rather than as turns.
        out.push({
          type: 'standalone',
          span: root,
          descendants: flattenDescendants(root.span_id),
        })
      }
    }
    return out
  })

  // Turn-anchored entries: real user prompts only. Background-task
  // notifications nest under the previous prompt, so they no longer
  // appear as separate rows in the Turns TOC or the spine timeline.
  const promptGroups = computed(() =>
    entries.value.filter((e) => e.type === 'group'),
  )

  const turnItems = computed(() => {
    const turnList = turns()
    return promptGroups.value.map((entry, idx) => {
      const toolCounts = {}
      for (const { span } of entry.descendants) {
        const name = span.name
        if (name.startsWith('tool.')) {
          toolCounts['tool'] = (toolCounts['tool'] || 0) + 1
        } else if (name === 'skill.read' || name === 'skill.invoke') {
          toolCounts['skill'] = (toolCounts['skill'] || 0) + 1
        } else if (name === 'file.edit' || name === 'plan.edit') {
          toolCounts['edit'] = (toolCounts['edit'] || 0) + 1
        }
      }
      // Pair with the backend `turns[]` record (if loaded) by
      // chronological index — both lists are 1:1 aligned:
      // turn[i] ⇔ promptGroup[i].
      const turn = turnList ? turnList[idx] || null : null
      const startTime = entry.prompt.start_time
      const lastSpan =
        entry.descendants[entry.descendants.length - 1]?.span || entry.prompt
      const endTime = lastSpan.end_time || lastSpan.start_time || startTime
      const startMs = new Date(startTime).getTime()
      const endMs = new Date(endTime).getTime()
      const promptText = entry.prompt.attributes?.text || 'prompt'
      return {
        idx,
        promptSpanId: entry.prompt.span_id,
        promptText,
        timestamp: startTime,
        startMs,
        endMs,
        durationMs: Math.max(0, endMs - startMs),
        toolCounts,
        turn,
      }
    })
  })

  // Phase-anchored TOC for workflow runs (parallel to turnItems): one row
  // per phase with its agents as sub-items. Empty for normal sessions.
  const phaseItems = computed(() => {
    if (!isWorkflow.value) return []
    const rootId = workflowRoot.value?.span_id || null
    // Only a *live* run (status still 'running') gets per-phase in-progress
    // cues — a completed run's phases must stay neutral even if some agent
    // span carries a non-'done' state.
    const liveRun = workflowRoot.value?.attributes?.workflow_status === 'running'
    const phases = _orderedPhases()
    const bands = phases.length
      ? phases.map((p) => ({ phase: p, agents: childrenOf(p.span_id).filter((s) => s.name === 'subagent.start') }))
      : (rootId ? [{ phase: null, agents: childrenOf(rootId).filter((s) => s.name === 'subagent.start') }] : [])
    return bands.map((b, idx) => {
      const doneCount = b.agents.filter(
        (a) => (a.attributes?.state || '') === 'done').length
      return {
        idx,
        // The `phase === null` band is the live/in-progress one: its agents
        // aren't phase-mapped yet (the manifest with phaseIndex only lands at
        // completion), so the rail renders it as a plain "Running" group rather
        // than a numbered phase.
        running: b.phase === null,
        // Every agent in the phase has finished → the header shows a ✓ instead
        // of the phase number (mirrors the terminal's "✓ Scope 1/1").
        complete: b.agents.length > 0 && doneCount === b.agents.length,
        // A real (numbered) phase still has unfinished agents on a live run:
        // render the completed-style header but with a running cue.
        inProgress: liveRun && b.phase !== null && doneCount < b.agents.length,
        phaseSpanId: b.phase?.span_id || `wf-live-${idx}`,
        title: b.phase?.attributes?.title || 'Agents',
        detail: b.phase?.attributes?.detail || '',
        index: b.phase?.attributes?.index ?? (idx + 1),
        agentCount: b.agents.length,
        doneCount,                       // finished agents (the "done" in done/total)
        // Phase token total = sum of its agents' output tokens. Null (not 0)
        // for live/in-progress bands where no agent has reported tokens yet,
        // so the rail can hide the chip instead of showing a bare "0".
        tokens: b.agents.reduce((s, a) => s + (a.attributes?.tokens || 0), 0) || null,
        agents: b.agents.map((a) => ({
          spanId: a.span_id,
          label: a.attributes?.label || a.attributes?.agent_type
            || (a.attributes?.agent_id || '').slice(0, 8) || 'agent',
          type: a.attributes?.agent_type || '',
          // Full model id (e.g. claude-opus-4-8[1m]); the rail shortens it
          // for display via fmtModel and keeps the full id in a tooltip.
          // Null on live runs, which don't capture per-agent model yet.
          model: a.attributes?.model ?? null,
          tokens: a.attributes?.tokens ?? null,
          toolCalls: a.attributes?.tool_calls ?? null,
          state: a.attributes?.state || '',
          done: (a.attributes?.state || '') === 'done',
          // Actively running (vs queued / stopped) — drives a subtle pulse.
          running: ['running', 'progress', 'start', 'in_progress']
            .includes(a.attributes?.state),
        })),
      }
    })
  })

  // True once the run has real `workflow.phase` spans (built from the
  // completion manifest). False for a live run — where the rail instead shows
  // the declared `phasePlan` below.
  const hasPhaseSpans = computed(() => _orderedPhases().length > 0)

  // The declared phase plan (title + detail, ordered) parsed from the workflow
  // script at ingest and stamped on the live run root. Available before the
  // completion manifest exists, so the rail can preview the planned phases
  // while agents are still running (they can't be phase-mapped live).
  const phasePlan = computed(() => workflowRoot.value?.attributes?.phase_plan || [])

  return {
    spanById, childrenByParent, rootSpans,
    childrenOf, flattenDescendants,
    entries, promptGroups, turnItems,
    isWorkflow, phaseItems, hasPhaseSpans, phasePlan,
  }
}
