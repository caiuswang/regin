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

  function flattenDescendants(spanId, depth = 0) {
    const children = childrenOf(spanId)
    const result = []
    for (const child of children) {
      result.push({ span: child, depth })
      const grandchildren = childrenOf(child.span_id)
      if (grandchildren.length) {
        result.push(...flattenDescendants(child.span_id, depth + 1))
      }
    }
    return result
  }

  const entries = computed(() => {
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

  return {
    spanById, childrenByParent, rootSpans,
    childrenOf, flattenDescendants,
    entries, promptGroups, turnItems,
  }
}
