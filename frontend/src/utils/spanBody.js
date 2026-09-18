// The resolver for "which attribute carries this span's readable body".
//
// Three surfaces used to answer this question from three hand-maintained
// lists, and the three had drifted: Focus's condensed row opened a
// `task.notification`'s raw `<task-notification>` envelope while the
// presentation reader showed its `summary`; `assistant.thinking` could never
// promote out of the fold because the failure probe was the only one of the
// three that did not read `thinking_text`; and a `subagent.stop`'s
// `result_preview` reached none of them.
//
// READ THIS BEFORE CHANGING HOW A SPAN'S BODY RENDERS. This file used to call
// itself "the ONE resolver" and it is not one — editing it reaches FOUR
// surfaces and misses the rest, which is how the same fix has had to be made
// twice in a row (ac12839f fixed the feed card and left the reader broken;
// eb0eaadf fixed the reader):
//
//   reached  presentation reader (presentModel.proseFor), its content-only
//            walk filter (spanHasBody), the search palette's preview pane
//            (SearchSpanPreview → presentFor), Focus's condensed row
//            (dispatch.condensedBody → CondensedRow)
//   missed   the search INDEX — searchDocs.js carries its own function also
//            named `spanBody`, and its key list knows 6 kinds to this file's
//            19; every feed card (Thinking / AssistantResponse / Recap /
//            ServerTool / Prompt / Compaction / Rewind) hardcodes its own
//            key; the agent result and brief (useAgentLaunchMerge's
//            `agentResultText` / `agentDescription` / `agentPrompt`, plus the
//            gates in useSpanTree and SubagentCard that decide whether a
//            result card exists at all); the timeline and /live rows
//            (traceFormatters `rowParts` / `fullLabel`, liveRows `humanMain`);
//            the detail rail (SpanDetailPanel); and the agent roster and live
//            sheet, which read roster ENTRIES rather than spans and so cannot
//            be reached from a span-keyed resolver at all.
//
// The missed surfaces are not all delegable: the index appends `file_path` /
// `diff` / `stdout` and short-circuits on `redacted`, and every row builder
// flattens and hard-truncates. But grep the attribute key you are changing
// across src/ before calling a body fix done.
//
// `verbatim` marks a body that is literal text — a file, a git status, an
// injected block — rather than model-authored markdown. A reader that renders
// it through the markdown pipeline eats its indentation and swallows anything
// that looks like a mark.
import { isSendToUserSpan } from './spanAttributes.js'
import { prettyJson } from './jsonBody.js'

// name → { label, keys: [preferred, …fallbacks], verbatim?, verbatimKeys? }
export const SPAN_BODY_SPEC = {
  // What the user typed, byte for byte — the one body on this table that is
  // the INPUT rather than a model's output. Rendered as markdown, pasted
  // source loses its indentation and every mark-shaped character:
  // `import java.util.*;` painted as `import java.util.;` and a `/* … */`
  // comment collapsed into emphasis. The conversation card has always
  // rendered it plain (`PromptBody`); this is the reader agreeing.
  prompt: { label: 'Prompt', keys: ['expanded_text', 'text'], verbatim: true },
  assistant_response: { label: 'Response', keys: ['text'] },
  'assistant.thinking': { label: 'Reasoning', keys: ['thinking_text'] },
  'harness.recap': { label: 'Recap', keys: ['content'] },
  'harness.skill_listing': { label: 'Skills', keys: ['content'], verbatim: true },
  // `result` is the finished agent's whole report (median 5.6 KB); `summary`
  // is the one-line "Agent X finished" that used to be the only body here.
  'task.notification': { label: 'Agent report', keys: ['result', 'summary'] },
  'tool.failure': { label: 'Failure', keys: ['error'], verbatim: true },
  // Key order is preference, and every key here is one the ingest really
  // writes — a spec key that exists on zero spans is the silent-render-nothing
  // bug this module was extracted to end. Counts against the live store:
  // subagent.start prompt 1597 / prompt_preview 46 / description 26 of 4757;
  // subagent.stop result_preview 2823 of 3177 (result_full: zero).
  // A finished agent's card IS its report, so the reader opens on the answer
  // and keeps the brief as the fallback for one still running. The answer keys
  // are stamped at serve time (`projection._stamp_agent_result`) — the raw
  // `result_preview` on the stop marker is a whitespace-flattened 200-char cut
  // and never the document a reader should show.
  'subagent.start': {
    label: 'Agent brief',
    keys: ['agent_segment_result', 'agent_result_preview',
           'prompt', 'prompt_preview', 'description'],
    labels: {
      agent_segment_result: 'Agent result',
      agent_result_preview: 'Agent result',
    },
    // Split by key, not by span: the result keys are the agent's own markdown
    // report, but the brief keys are the INPUT text handed to the agent —
    // literal like a typed `prompt`, and a launch prompt is mostly
    // instructions, paths and fenced source. Rendered as markdown its
    // indentation collapsed and every `*`/`_`/`#` in a path or snippet was
    // eaten.
    verbatimKeys: ['prompt', 'prompt_preview', 'description'],
  },
  'subagent.stop': { label: 'Agent result', keys: ['result_preview'] },
  'tool.Agent': {
    label: 'Agent brief',
    keys: ['prompt', 'prompt_preview', 'description'],
    verbatimKeys: ['prompt', 'prompt_preview', 'description'],
  },
  'workflow.agent_result': { label: 'Agent result', keys: ['result', 'result_preview', 'text'] },
  'tool.Read': { label: 'File', keys: ['content'], verbatim: true },
  'compact.post': { label: 'Compaction summary', keys: ['summary'] },
  'memory.recall': { label: 'Injected block', keys: ['block'], verbatim: true },
  'environment.git_status': { label: 'Git status', keys: ['block'], verbatim: true },
  'skill.invoke': { label: 'Arguments', keys: ['command_args'], verbatim: true },
  'proposal.agent.stdout': { label: 'Output', keys: ['chunk'], verbatim: true },
}

const EMPTY = { label: '', key: '', text: '', verbatim: false }

// An agent's REPORT that turns out to be a JSON document is literal text, not
// markdown: the renderer finds no structure in it and flattens the payload
// into one paragraph. Pretty-print it and show it the way a file is shown.
//
// Only a body the spec left as prose. An already-verbatim body is bytes the
// reader must see unchanged — a `tool.Read` of package.json re-indented here
// would round its 64-bit ints, decode its escapes and drop a duplicate key,
// and a pasted-JSON `prompt` would stop being what the user typed.
function asJsonBody(body) {
  if (body.verbatim) return body
  const pretty = prettyJson(body.text)
  return pretty ? { ...body, text: pretty, verbatim: true } : body
}

// A key whose value is not a non-empty STRING is not a body: several kinds
// carry a list or an object under a name this table also uses for prose
// (`harness.task_reminder.content` is a 769-element array), and rendering one
// as text prints `[object Object]` where the reader promised a document.
export function spanBody(span) {
  const a = span?.attributes || {}
  if (isSendToUserSpan(span)) {
    return { label: 'Message body', key: 'user_message', text: a.user_message || '', verbatim: false }
  }
  const spec = SPAN_BODY_SPEC[span?.name]
  if (spec) {
    const key = spec.keys.find(k => typeof a[k] === 'string' && a[k])
    return asJsonBody({
      label: (key && spec.labels?.[key]) || spec.label,
      key: key || spec.keys[spec.keys.length - 1],
      text: key ? a[key] : '',
      verbatim: !!spec.verbatim || !!(key && spec.verbatimKeys?.includes(key)),
    })
  }
  if (a.server_side && typeof a.response_text === 'string' && a.response_text) {
    return { label: 'Result', key: 'response_text', text: a.response_text, verbatim: false }
  }
  return EMPTY
}
