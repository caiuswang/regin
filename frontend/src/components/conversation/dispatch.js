/**
 * Does this span render as a card in the conversation feed?
 *
 * ConversationSpanCard is a v-if/v-else-if chain with NO catch-all branch, so
 * a span whose name matches nothing produces an empty row. That was papered
 * over in CSS (`.event-spine-row:not(:has(...))` in style.css) and separately
 * mis-counted in the turn header, which claimed "259 events" over 61 rendered
 * rows because it counted spans the chain silently drops.
 *
 * This is the single source of truth for that question. It MUST mirror the
 * branches in ConversationSpanCard.vue — `conversation-event-count.spec.js`
 * asserts the two agree by comparing the header count against the rows the
 * DOM actually shows, so a new branch added here or there fails a test rather
 * than drifting quietly.
 */
import { isAskSpan, runStateOf } from '../../utils/liveRows.js'
import { searchText } from '../../utils/plainText.js'
import { isSendToUserSpan } from '../../utils/spanAttributes.js'
import { spanBody } from '../../utils/spanBody.js'

const EXACT_NAMES = new Set([
  'task.notification',
  'assistant.thinking',
  'assistant_response',
  'tool.failure',
  'tool.ToolSearch',
  'rule.check',
  'memory.recall',
  'harness.local_command',
  'harness.recap',
  'harness.api_retry',
  'workflow.phase',
  'subagent.start',
  'workflow.agent_result',
  'tool.Workflow',
  'skill.read',
  'skill.invoke',
  'file.edit',
  'plan.edit',
])

// Roster agents whose rows do NOT belong in this feed. A span tagged with a
// roster agent's id is served by that agent's own child trace, so the parent
// feed shows the agent's card alone — but a SCOPED feed (the companion pane,
// the <xl takeover) IS that child trace, and every row there carries its id.
//
// The distinction only bites after a body fetch: the child's `/map` projection
// strips `agent_id` out of the bag, so the scoped rows render until the reader
// clicks one, and the write-once content cache then hides it for good.
export function foreignAgentIds(agents, scopeAgent) {
  const own = scopeAgent ? scopeAgent.agentId : null
  return new Set(agents.map((a) => a.agentId).filter((id) => id !== own))
}

export function rendersInConversation(span) {
  const name = span?.name
  if (!name) return false
  const attrs = span.attributes || {}

  // Serve time moves a finished agent's answer onto its `subagent.start` card
  // (`projection._carry_answer`) and names that card here. The stop row would
  // then print the same answer a line below it, as the flattened 200-char cut
  // it reports — so the row that was carried is not a row.
  if (name === 'subagent.stop' && attrs.agent_result_carried_by) return false
  // Every attribute-conditional branch in the chain (Bash with output,
  // Edit/Write with a diff, AskUserQuestion with questions) falls through to
  // the generic inline row when its condition fails, so for "does anything
  // render" the tool prefixes settle it on their own.
  if (name.startsWith('tool.') || name.startsWith('subagent.')) return true
  if (attrs.server_side && attrs.response_text) return true
  // A parked ask reaches the feed as ONE `permission.request` — the serve-time
  // merge collapses its `tool.AskUserQuestion` placeholder into it — and that
  // name matches no branch above, so the question the agent is blocked on
  // rendered as nothing at all. Same predicate the /live card routes on.
  if (isAskSpan(span) && attrs.questions) return true
  return EXACT_NAMES.has(name)
}

// ── Focus-mode render tiers ──────────────────────────────────────────
//
// Focus is a reading-density control, not a filter: every span still reaches
// the feed, it just reaches it at one of three prominences. `tierOf` lives
// here, beside `rendersInConversation`, because the turn header's
// `shown · folded` count and the card chain MUST read the same predicate —
// counting one and rendering the other is how "259 events" over 61 rows
// happened the first time.
export const TIER_CHROME = 0
export const TIER_FULL = 1
export const TIER_CONDENSED = 2
export const TIER_FOLDED = 3

// The narrative spine: turn structure, anything that changed the world or
// failed, and the boundaries where a new actor enters. Renders identically
// with Focus on or off.
const TIER_FULL_NAMES = new Set([
  'prompt', 'assistant_response', 'permission.request',
  'tool.AskUserQuestion', 'tool.ExitPlanMode', 'plan.exit',
  'tool.Edit', 'tool.Write', 'tool.MultiEdit', 'tool.apply_patch',
  'tool.NotebookEdit', 'file.edit',
  'tool.failure', 'permission.denied', 'turn.cancel', 'rewind',
  'subagent.start', 'tool.Agent', 'tool.Workflow', 'tool.Skill',
  // A phase is the workflow's turn structure — the emerald band that says
  // where you are in the run. Condensing it replaced the divider with a row.
  'workflow.phase', 'workflow.agent_result',
])

// Folded into the turn's single strip. `assistant.thinking` is here because
// it is the agent talking to itself rather than to the reader, and at ~75k
// spans it is the loudest voice in the store; `rule.check` because 20k of
// them would drown the story whether they passed or not.
const TIER_FOLDED_NAMES = new Set([
  'assistant.thinking', 'rule.check',
  'harness.task_reminder', 'harness.tools_delta', 'harness.skill_listing',
  'harness.local_command', 'harness.recap', 'hook.stop_summary',
  'cwd.changed', 'instructions.loaded', 'environment.git_status',
  'config.change', 'session.title',
  'tool.ToolSearch', 'memory.recall', 'memory.recall.task',
  'memory.index.nav', 'skill.read', 'skill.launch',
])

// Promotion matches failure SHAPES, not the word "error" — otherwise a clean
// `✓ no type errors` run promotes itself out of the fold. The exemption is
// stripped before the failure probe runs, so both can appear in one blob.
const NO_FAILURE_RE = /\b(?:no|0|zero)\s+(?:\w+\s+){0,2}(?:errors?|failures?|violations?|problems?)\b/gi
const FAILURE_RE = /(?:\bexit\s+[1-9]\d*\b|\b[1-9]\d*\s+(?:errors?|failures?|violations?)\b|✗)/i

// Only the fields that exist BECAUSE something went wrong. The probe used to
// read `stdout`, the rendered body, `text`, `content` and `result_preview` as
// well, which made any span whose output DISCUSSED a failure read as one:
// across the live store that is 2,886 non-error spans — 1,681 `tool.Bash` that
// exited 0 and printed a test log (a non-zero exit becomes its own
// `tool.failure` span, so a surviving Bash succeeded), 708 `assistant_response`
// where the agent wrote "24 errors (unchanged)", 477 `tool.Read` of a file
// containing `✗`. Each of them was then kept unconditionally by `keptByFocus`
// and bucketed as a failure by `foldCategory`, so a mid-turn grep outranked
// the turn's own answer and the strip reported problems that had not happened.
// Content is not status.
export function isFailureShaped(span) {
  if (!span) return false
  const a = span.attributes || {}
  // `status_code` is the field the span API serves (the DB column); a bare
  // `span.status` is undefined on every span, so reading it promoted nothing.
  if (span.status_code === 'ERROR' || a.status === 'ERROR') return true
  if (a.denied || a.rejected) return true
  // `error` needs no shape test: unlike stderr, which a successful command
  // writes progress to, it is only written when the call failed. Probing it
  // missed the store's most common one — `Exit code 1` reads as prose to a
  // regex looking for `exit 1`.
  if (typeof a.error === 'string' && a.error) return true
  if (typeof a.stderr !== 'string' || !a.stderr) return false
  return FAILURE_RE.test(a.stderr.replace(NO_FAILURE_RE, ''))
}

// Did this span fail, by any of the ways a span records that? `tool.failure`
// and `permission.denied` say so by NAME — a backfilled one can reach the feed
// with neither an error attribute nor an ERROR status — and everything else
// says so in its status or its error output.
export function didFail(span) {
  const name = span?.name
  return name === 'tool.failure' || name === 'permission.denied' || isFailureShaped(span)
}

export function tierOf(span) {
  const name = span?.name
  if (!name) return TIER_CHROME
  // A span still in flight is the one row the reader came for, whatever its
  // name would otherwise tier it as: condensing it to a one-liner hides the
  // only thing on the page that is still moving.
  if (runStateOf(span) === 'running' && rendersInConversation(span)) return TIER_FULL
  // The chrome guard runs FIRST, before either name list. A span the card
  // chain has no branch for never reaches the feed in either mode, so its
  // tier is chrome whatever its name suggests — 12 of the folded names and 6
  // of the full ones are in that position (`cwd.changed`, `prompt`, `rewind`,
  // …; the last two render as turn chrome, not as spine rows). Tiering them
  // by name made them look foldable to every tier-reading caller: `unfoldFor`
  // sprang an unrelated turn's fold open for a span that renders nothing.
  if (!rendersInConversation(span)) return TIER_CHROME
  if (TIER_FULL_NAMES.has(name)) return TIER_FULL
  // The agent addressing the user is always the narrative spine, whichever
  // server id it came through: the literal `tool.mcp__send-to-user__…` this
  // list used to carry missed the plugin-namespaced and bare spellings, and
  // those 254 spans condensed into a bodyless one-liner.
  if (isSendToUserSpan(span)) return TIER_FULL
  if (isAskSpan(span) && span.attributes?.questions) return TIER_FULL
  if (TIER_FOLDED_NAMES.has(name)) {
    // `rule.check` folds pass OR violation — the violation is not lost, it
    // rides the strip as a `⚠ n` badge. Every other folded span promotes to
    // condensed on the occurrence that failed.
    if (name === 'rule.check') return TIER_FOLDED
    return isFailureShaped(span) ? TIER_CONDENSED : TIER_FOLDED
  }
  return TIER_CONDENSED
}

// Does this row survive Focus's keep-set?
//
// Focus answers "what did this turn DO", so the spine keeps the agent's own
// outcome, the spans that changed the world, and anything that failed. It is
// a keep-set rather than a positional pick because the positional one was
// measurably wrong: over 258 turns of the live store, the turn's LAST span was
// a folded-tier one (`harness.recap`, `assistant.thinking`,
// `harness.local_command`, `rule.check`) in 61 of them — a quarter of the feed
// headlined by the noise the mode exists to remove.
//
// A subagent is ONE unit here: its internals stay behind its own card even
// when the reader expanded it, because a Focus turn describes what the parent
// agent did, and 200 flattened child rows answer a different question. A
// failure inside a child is the exception — that is the one thing worth
// interrupting the parent's story for.
//
// `workflow.agent_result` is `inAgent` too (`useSpanTree._pushAgent` files it
// under the agent so it draws inside that rail), but it is the agent's OUTCOME,
// not one of its steps. Setting it aside hid the answer of every agent in a
// workflow trace — the exact opposite of the mode's job.
const AGENT_OUTCOME_NAMES = new Set(['workflow.agent_result'])

export function keptByFocus(row) {
  if (!row?.span) return false
  // Tier 3 is never kept, even when it failed. The one span that reaches here
  // failure-shaped AND still folded is a violating `rule.check` — `tierOf`
  // pins those to the fold deliberately, and the violation is not lost: it
  // rides the strip as a ⚠ badge. Keeping it would put a row on the spine that
  // `isKeptRow` still reads as hidden, so selecting it sprang the whole strip.
  if (row.tier === TIER_FOLDED) return false
  if (isFailureShaped(row.span)) return true
  if (row.inAgent && !AGENT_OUTCOME_NAMES.has(row.span.name)) return false
  return row.tier === TIER_FULL
}

// What a COLLAPSED turn's chip preview may advertise: the turn's WORK, in
// every mode — its tool calls, shell runs, edits and replies — with only the
// tier-3 noise (`rule ×20`, `recall ×3`, thinking) left out, because promising
// those on a turn whose spine folds them made two thirds of the chips vanish on
// expand.
//
// Focus used to narrow this further, to its own one-card keep-set, so that a
// closed Focus turn could not advertise the shell runs its open turn hides.
// That is the wrong symmetry. A strip hides nothing: its rows stay on the
// spine, one click away, under a mix bar that names them. A COLLAPSED turn has
// no strip — the chips are the entire account of what the turn did, and a
// mode-narrowed account gives the reader no way to discover its own omissions.
// A turn that ran three test suites read as `Edit · failure`, and the only way
// to learn a shell had run at all was to expand the turn.
export function chipSpans(descendants, mode) {
  if (mode !== 'condensed' && mode !== 'latest') return descendants
  return descendants.filter(({ span }) => tierOf(span) !== TIER_FOLDED)
}

// The buckets behind the strip's mix bar and its screen-reader line.
// Focus sets aside EVERY earlier span of a turn, not just the tier-3 ones, so
// the buckets below have to name work as well as noise — a strip that called a
// turn's edits and shell runs "context" would be describing the wrong session.
const EDIT_NAMES = new Set([
  'tool.Edit', 'tool.Write', 'tool.MultiEdit', 'tool.NotebookEdit',
  'tool.apply_patch', 'file.edit',
])

export function foldCategory(span) {
  const n = span?.name || ''
  // `rule.check` first: a violating one is counted as lint, not as a failure,
  // because the strip already carries it as a ⚠ badge and double-reporting it
  // would make one violation look like two problems.
  if (n === 'rule.check') return 'rule'
  // Shape, not name, and BEFORE the name buckets. Focus sets aside everything
  // but the turn's one card, failures included, so a failing `tool.Bash` that
  // bucketed as a plain `tool calls` left the reader no signal at all that
  // something in there went wrong.
  if (isFailureShaped(span)) return 'failures'
  if (n === 'assistant.thinking') return 'thinking'
  if (n.startsWith('harness.') || n === 'hook.stop_summary') return 'harness'
  // Of the names that actually fold, three used to collapse into one generic
  // `context` bucket — a strip reading "context" conflated deferred-tool
  // searches with injected memory with skill reads.
  if (n === 'tool.ToolSearch') return 'tools'
  if (n.startsWith('memory.')) return 'memory'
  if (n.startsWith('skill.')) return 'skills'
  if (EDIT_NAMES.has(n)) return 'edits'
  if (n === 'assistant_response') return 'replies'
  if (n === 'tool.failure' || n === 'permission.denied') return 'failures'
  if (n.startsWith('subagent.') || n === 'tool.Agent' || n.startsWith('workflow.')) return 'agents'
  if (n.startsWith('tool.')) return 'tool calls'
  return 'context'
}

// Every label is written out per category rather than derived: the obvious
// derivation, `s.replace(/s$/, '')`, yields "harnes".
export const FOLD_CATEGORY_LABELS = {
  rule: 'rule checks',
  context: 'context',
  thinking: 'thinking',
  harness: 'harness',
  tools: 'tool search',
  memory: 'memory',
  skills: 'skill reads',
  edits: 'edits',
  replies: 'replies',
  failures: 'failures',
  agents: 'agents',
  'tool calls': 'tool calls',
}

// Inline body a condensed row can open. A Tier 2 span with nothing here gets
// no disclosure caret at all — 60 rows of dead chevrons is exactly the per-row
// chrome the density budget can't afford.
// A condensed row's disclosure is a PREVIEW inside a 26px row, not a file
// viewer. `max-height: 40vh` bounds what is painted, not what is put in the
// DOM — and now that the body resolver reaches `tool.Read.content`, an
// unbounded return injects a whole 62KB file per row, tens of rows per turn.
const CONDENSED_MAX = 4000
const cap = (text) => (text.length <= CONDENSED_MAX
  ? text
  : `${text.slice(0, CONDENSED_MAX)}\n… ${text.length - CONDENSED_MAX} more characters — open the reader (⤢) for the whole document.`)

export function condensedBody(span) {
  const a = span?.attributes || {}
  // stdout/stderr are literal output — never markdown — so they pass through
  // untouched; the prose fallbacks are excerpts and get the plain-text rule.
  const out = [a.stdout, a.stderr].filter(Boolean).join('\n')
  if (out) return cap(out)
  // `spanBody` before the generic keys: several kinds carry BOTH a semantic
  // body and a raw `text` envelope, and reading `text` first opened the
  // `<task-notification>` XML instead of the sentence a human wrote.
  //
  // `searchText`, not `plainText`: this <pre> is a 40vh scrollable READING
  // surface, and plainText collapses every fenced block to the literal token
  // `[code]` — which is right for a one-line excerpt and destroys the content
  // here (an advisor or server-tool answer is mostly code).
  const body = spanBody(span)
  if (body.text) return cap(body.verbatim ? body.text : searchText(body.text))
  return cap(searchText(a.thinking_text || a.response_text || a.text || ''))
}

// Tier 2 swaps the card chain's renderer for CondensedRow. That is a pure
// loss when the chain's own branch is ALREADY a collapsed one-liner: BashCard
// rests at the same 26px, and everything behind its caret — the dark
// command/stdout/stderr panel, the per-section copy buttons, the
// `truncated N B` badges — plus the `interrupted` / `background` /
// return-code marks it carries at rest have nowhere to go in a condensed row.
//
// The attribute guard mirrors BashCard's branch in ConversationSpanCard.vue,
// deliberately: a `tool.Bash` carrying none of these falls through to the
// bodyless InlineToolRow, and there the condensed row is a real gain.
//
// `hasTaskSnapshot` is passed in rather than read here: the replayed task-list
// snapshot lives in a map the hosting view provides, and this module must stay
// pure over the span so the header count and the card chain can share it.
export function keepsOwnCard(span, hasTaskSnapshot = false) {
  if (hasTaskSnapshot) return true
  if (span?.name !== 'tool.Bash') return false
  const a = span.attributes || {}
  return Boolean(a.stdout || a.stderr || a.interrupted || a.command)
}
