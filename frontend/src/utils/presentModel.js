// presentFor(span, ctx) — the presentation reader's view model.
//
// Pure over the span, so the overlay template holds no per-kind conditionals
// beyond the `hasX` flags. Identity, vitals and attributes resolve through the
// SAME helpers the detail rail uses (spanLabel, the formatters,
// visibleAttributeKeys): the reader adds formatting, never a second opinion
// about what a span is.
import {
  spanLabel, fmtDuration, fmtTokens, fmtCost, categoryOf, diffFileName, diffOpLabel,
  askOptLabel, askOptDescription, askIsChosen, askFreeText, askNote, truncate,
  askModeLabel, askModeMark, askOptMark,
} from './traceFormatters.js'
import { barColor } from './spanColors.js'
import { visibleAttributeKeys, isSendToUserSpan, mcpCallFor } from './spanAttributes.js'
import { spanBody } from './spanBody.js'
import { isAskSpan } from './liveRows.js'
import { plainText, readingMeta } from './plainText.js'

// Which attribute carries this span's readable body now lives in
// `utils/spanBody.js`, shared with Focus's condensed row and the fold-promotion
// probe: three private copies of that table had drifted apart.
const proseFor = spanBody

// An AskUserQuestion round-trip. `questions`/`answers` are on the rail's
// ASK_HIDDEN_KEYS, so a span that renders no Q&A block here shows its payload
// NOWHERE — which is why the reader has to answer the same shape the feed
// card does rather than falling through to the attributes table.
function askFor(span) {
  const a = span.attributes || {}
  const questions = isAskSpan(span) ? (a.questions || []) : []
  if (!questions.length) return null
  return {
    denied: !!a.denied,
    denyLabel: a.denied ? (a.deny_kind === 'chat' ? 'chat instead' : 'denied') : '',
    // The harness injects this text when the user denies a call; it is not
    // user prose, and the feed card labels it as such.
    denialReason: a.denied ? (a.denial_reason || '') : '',
    questions: questions.map((q, qi) => ({
      key: qi,
      header: q.header || '',
      question: q.question || '',
      multi: !!q.multiSelect,
      modeLabel: askModeLabel(!!q.multiSelect),
      modeMark: askModeMark(!!q.multiSelect),
      options: (q.options || []).map((opt, oi) => ({
        key: oi,
        label: askOptLabel(opt),
        description: askOptDescription(opt),
        preview: (opt && opt.preview) || '',
        chosen: askIsChosen(span, q, opt),
        mark: askOptMark(!!q.multiSelect, askIsChosen(span, q, opt)),
      })),
      freeText: askFreeText(span, q),
      note: askNote(span, q),
    })),
  }
}

// A non-ask tool call the user denied. `hiddenKeysFor` strips `denial_reason`
// and `deny_kind` from the attributes table because the RAIL renders a deny
// panel for exactly this shape — so a reader with no panel of its own showed
// "metadata only" over the one interesting fact about the span.
function denyFor(span) {
  const a = span.attributes || {}
  if (!a.denied || isAskSpan(span)) return null
  return {
    label: a.deny_kind === 'chat' ? 'chat instead' : 'denied',
    reason: a.denial_reason || '',
    droppedBytes: a.denial_reason_truncated_bytes || 0,
  }
}

// The MCP request/response round-trip. `MCP_HIDDEN_KEYS` takes `mcp_input`
// and `mcp_result` out of the attributes table on the rail's authority, and
// the rail earns that by rendering McpCallDetail. Without the same block here
// the reader claimed 3,244 spans carried nothing at all while the 340px rail
// beside it showed the whole exchange.
function mcpFor(span) {
  const call = mcpCallFor(span)
  if (!call) return null
  const asText = (v) => {
    if (v == null) return ''
    return typeof v === 'string' ? v : JSON.stringify(v, null, 2)
  }
  return {
    input: asText(call.input),
    result: asText(call.result),
    inputDropped: call.inputDropped || 0,
    resultDropped: call.resultDropped || 0,
  }
}

function askCopyText(ask) {
  const lines = []
  for (const q of ask.questions) {
    if (q.header) lines.push(`## ${q.header}`)
    lines.push(q.question)
    lines.push(`(${askModeLabel(q.multi)})`)
    for (const opt of q.options) {
      lines.push(`${opt.chosen ? '[x]' : '[ ]'} ${opt.label}${opt.description ? ` — ${opt.description}` : ''}`)
    }
    if (q.freeText) lines.push(`[x] (other) ${q.freeText}`)
    if (q.note) lines.push(`Note: ${q.note}`)
    lines.push('')
  }
  if (ask.denialReason) lines.push(`Denied: ${ask.denialReason}`)
  return lines.join('\n').trim()
}

// §07 shell colorization. Order matters: failure beats success, so a line
// carrying both "✓ 3 passed" and "✗ 1 failed" reads as failure.
const TERM_ECHO = /^\s*[>$]/
const TERM_FAIL = /✗|error|fail/i
const TERM_OK = /✓|passed/i
export function termLineColor(text) {
  if (TERM_ECHO.test(text)) return 'term-echo'
  if (TERM_FAIL.test(text)) return 'term-fail'
  if (TERM_OK.test(text)) return 'term-ok'
  return 'term-plain'
}

function termFor(span) {
  const a = span.attributes || {}
  const out = [a.stdout, a.stderr].filter(Boolean).join('\n')
  const cmd = a.command || a.command_preview || ''
  // The gate used to test `a.command` while the line below rendered
  // `command || command_preview`, so the block refused to draw content it was
  // perfectly willing to produce — 38,777 Bash spans (every in-flight one, and
  // every completed one whose output was not captured) read "metadata only".
  if (!out && !cmd) return { has: false, cmd: '', lines: [] }
  return {
    has: true,
    cmd,
    lines: out ? out.split('\n').map(text => ({ text, color: termLineColor(text) })) : [],
  }
}

// What a shell command did to the files it touched — the same four attributes
// the feed's Bash card reads. Without a block of its own the reader had no
// choice but the attributes table, which printed the diff strings with their
// newlines escaped: strictly less readable than the 340px rail beside it.
function bashEditsFor(span) {
  const a = span.attributes || {}
  const edits = Array.isArray(a.bash_edits) ? a.bash_edits : []
  const files = Array.isArray(a.bash_edit_files) ? a.bash_edit_files : []
  if (!edits.length && !files.length) return null
  const shown = new Set(edits.map(e => e.file_path))
  return {
    files: edits.map((e, i) => ({
      key: `${e.file_path}-${i}`,
      filePath: e.file_path || '',
      op: diffOpLabel(e.edit_op || ''),
      added: e.added_lines || 0,
      removed: e.removed_lines || 0,
      diff: e.diff || '',
      droppedBytes: e.diff_truncated_bytes || 0,
    })),
    // Paths Claude Code named but shipped no hunks for — the capped overflow.
    extraPaths: files.filter(p => !shown.has(p)),
    more: a.bash_edit_more || 0,
    // The session the paths resolve against when the reader opens one.
    traceId: span.trace_id || '',
    unavailable: !!a.bash_edit_unavailable,
  }
}

function rulesFor(span) {
  if (span.name !== 'rule.check') return null
  const a = span.attributes || {}
  const list = Array.isArray(a.applicable_rules) ? a.applicable_rules : []
  // `RULE_HIDDEN_KEYS` strips status / relative_path / the three counts from
  // the attributes table on the rail's authority, and the rail earns that by
  // rendering them as chips. Without them here a check that matched no rule —
  // ~16% of them — showed "metadata only" and an attributes panel with neither
  // the file nor the verdict: strictly less than the 340px rail it expands on.
  return {
    file: a.relative_path || a.file_path || '',
    status: a.status || '',
    applicable: a.applicable_rule_count ?? list.length,
    violating: a.violating_rule_count ?? list.filter(r => r.violated).length,
    total: a.total_rules ?? null,
    items: list.map(r => ({
      id: r.id,
      violated: !!r.violated,
      mark: r.violated ? '✗' : '✓',
      summary: r.summary || '',
      guide: r.guide || '',
      matchCount: r.match_count || 0,
    })),
  }
}

function chipsFor(span) {
  const a = span.attributes || {}
  const tokens = (span.input_tokens || span.output_tokens)
    ? `${span.input_tokens ? fmtTokens(span.input_tokens) : '0'} / ${span.output_tokens ? fmtTokens(span.output_tokens) : '0'}`
    : ''
  return [
    { k: 'duration', v: fmtDuration(span.duration_ms) || '' },
    { k: 'tokens', v: tokens },
    { k: 'cost', v: span.cost_usd ? fmtCost(span.cost_usd) : '' },
    // Only a real failure earns a chip. `UNSET` is what an ordinary span
    // carries, so surfacing it fills the vitals line with a word that means
    // "nothing happened".
    { k: 'status', v: span.status_code === 'ERROR' ? 'ERROR' : '' },
    { k: 'file', v: a.file_path ? a.file_path.split('/').pop() : '' },
    // Blank vitals are DROPPED, not rendered as "—": an em-dash column is
    // chrome that says nothing, and the header has three of them.
  ].filter(c => c.v)
}

// Mirrors `has_encrypted_thinking` in lib/trace/transcript_models.py: the API
// returned extended-thinking blocks whose plaintext it never handed over, only
// an encrypted signature. The reasoning is real and its tokens are billed —
// it simply cannot be shown — which is a different fact from a span that
// carries nothing, and the reader must not conflate the two.
export function hasEncryptedThinking(span) {
  const a = span?.attributes || {}
  return span?.name === 'assistant.thinking'
    && Number(a.thinking_blocks) > 0
    && !a.thinking_text
}

// Does this span carry something to READ, as opposed to bookkeeping? The
// "content only" walk filter (§11b) restricts ↑↓ to spans that pass — most
// spans in a long trace are reads, recalls and metadata, and stepping through
// them one at a time in a reading surface is noise.
//
// `hasChildren` counts ONLY for a subagent launch, whose child list IS its
// body in the reader. Treating "parents anything at all" as a body was wrong
// by 21.8k spans in this store alone: the ingest hangs a turn's tool spans off
// its `assistant.thinking` span, so every text-less thinking span looked
// readable and the filter never skipped one.
export function spanHasBody(span, hasChildren = false) {
  if (!span) return false
  const a = span.attributes || {}
  if (proseFor(span).text) return true
  if (isAskSpan(span) && (a.questions || []).length) return true
  if (a.diff) return true
  if (bashEditsFor(span)) return true
  // `termFor`, not a private key list: the two disagreed on a command-only
  // Bash, so the walk skipped spans the reader renders a `$ cmd` line for —
  // which is exactly the promise "content only" makes.
  if (termFor(span).has) return true
  if (mcpCallFor(span)) return true
  if (a.denied && (a.denial_reason || a.deny_kind)) return true
  if (span.name === 'rule.check' && (a.applicable_rules || []).length) return true
  return span.name === 'subagent.start' && hasChildren
}

// `categoryOf` buckets anything it has no chip for as 'other' — which is right
// for the Terminal's filter chips (a fixed, closed list) but reads as a
// mislabel in the reader's header, where a subagent launch announced itself as
// OTHER. Recover the obvious kind from the name's own namespace rather than
// widening the shared category list, which the filter bar enumerates.
const KIND_BY_PREFIX = [
  ['subagent.', 'subagent'], ['workflow.', 'workflow'], ['memory.', 'memory'],
  ['harness.', 'harness'], ['plan.', 'plan'], ['task.', 'task'], ['hook.', 'hook'],
]
function kindLabelFor(span) {
  const category = categoryOf(span)
  if (category !== 'other') return category
  const name = span.name || ''
  const hit = KIND_BY_PREFIX.find(([prefix]) => name.startsWith(prefix))
  return hit ? hit[1] : (name.split('.')[0] || 'other')
}

// The header is a fixed band above a `flex: 1` document. Two lines of 20px
// type at the panel's comfortable width is the budget; past that the title
// stops being a title and starts being the content it introduces.
const TITLE_MAX = 120

// A subject line is one line. A label built from a traceback or a multi-line
// command otherwise wrapped the header to the height of the payload.
const collapseWhitespace = (text) => String(text || '').replace(/\s+/g, ' ').trim()

// A pretty-printed JSON body opens on a bare `{`, which is no subject at all.
// Skip the structural line and land on the first key instead.
function firstSubjectLine(text) {
  return plainText(text).split('\n')
    .map(line => line.trim())
    .find(line => line && !/^[[{]$/.test(line))
}

export function presentFor(span, ctx = {}) {
  if (!span) return null
  const a = span.attributes || {}
  const prose = proseFor(span)
  const term = termFor(span)
  const bashEdits = bashEditsFor(span)
  const rules = rulesFor(span)
  const ask = askFor(span)
  const deny = denyFor(span)
  const mcp = mcpFor(span)
  const children = ctx.children || []
  const hasDiff = Boolean(a.diff)
  const hasProse = Boolean(prose.text)
  // The header's second line is a SUBJECT line. When the shared resolver has
  // nothing better than the raw span name — which is already the chip beside
  // it — a prose span reads its own first line instead. An ask always reads
  // the question it put to the user: neither label the resolver produces is a
  // subject (`permission.request` is the bare name, `tool.AskUserQuestion`
  // becomes "AskUserQuestion" — the same word as the chip).
  const label = spanLabel(span)
  const rawTitle = ask
    ? (ask.questions[0]?.question || label)
    : ((hasProse && label === span.name)
      ? (firstSubjectLine(prose.text) || label)
      : label)
  // ONE cap, applied last, on every branch. `spanLabel` is unbounded for
  // `tool.failure` (a 16,384-char error in this store), `harness.local_command`
  // (command + args), `tool.Grep`/`ToolSearch`/`WebFetch` — and the header band
  // is `flex-shrink: 0`, so an uncapped title grew it past the panel, squeezed
  // the document to nothing and pushed the ↑↓ footer off the viewport. The
  // three ellipsis-free `.slice()`s this replaces also cut mid-word with no
  // mark that anything was cut.
  const title = truncate(collapseWhitespace(rawTitle), TITLE_MAX)

  // Keys a dedicated block above the table already rendered.
  const extraHidden = [prose.key, 'diff', 'stdout', 'stderr', 'command', 'command_preview',
    'bash_edits', 'bash_edit_files', 'bash_edit_more', 'bash_edit_unavailable']
    .filter(Boolean)
  const attrKeys = visibleAttributeKeys(span, extraHidden)

  return {
    dot: barColor(span.name || ''),
    kindLabel: kindLabelFor(span),
    name: span.name,
    title,
    turnLabel: ctx.turnLabel || '',

    chips: chipsFor(span),
    readMeta: hasProse ? readingMeta(prose.text) : '',

    // Gated on content: `proseFor` returns the label unconditionally, so an
    // encrypted-thinking span (72k of them) painted a "REASONING" heading with
    // nothing under it, directly above the box explaining there is nothing.
    contentLabel: hasProse ? prose.label : '',
    proseVerbatim: !!prose.verbatim,
    hasProse,
    prose: prose.text,
    spanId: span.span_id,
    // The `[Image #N]` markers in a prompt's text resolve against THIS list;
    // the bytes endpoint is prompt-scoped, so no other span kind can carry one.
    imageIndices: span.name === 'prompt' && Array.isArray(a.image_indices)
      ? a.image_indices
      : [],
    // Everything that rendered, not the first match in a four-key ladder: on a
    // `tool.Bash` the ladder copied the command and silently dropped the
    // 30k-char stdout beside it, and on a rule-list or children-only span it
    // produced '' — `useCopy` bails on falsy input, so the button did nothing
    // at all and never even flashed.
    copyText: [
      prose.text,
      ask ? askCopyText(ask) : '',
      deny ? `Denied (${deny.label}): ${deny.reason}` : '',
      a.diff,
      bashEdits ? bashEdits.files.map(f => `${f.op} (${f.filePath})\n${f.diff}`).join('\n\n') : '',
      term.cmd ? `$ ${term.cmd}` : '',
      [a.stdout, a.stderr].filter(Boolean).join('\n'),
      mcp ? [mcp.input && `→ ${mcp.input}`, mcp.result && `← ${mcp.result}`].filter(Boolean).join('\n\n') : '',
      rules ? [
        `${rules.status || 'rule check'}${rules.file ? ` · ${rules.file}` : ''}`,
        ...rules.items.map(r => `${r.mark} ${r.id}${r.summary ? ` — ${r.summary}` : ''}`),
      ].join('\n') : '',
      children.length ? children.map(c => `· ${spanLabel(c)}`).join('\n') : '',
    ].filter(Boolean).join('\n\n'),

    hasAsk: Boolean(ask),
    ask,

    hasDiff,
    diff: a.diff || '',
    diffFile: diffFileName(span) || a.file_path || '',
    // `edit_op` / `added_lines` / `removed_lines` — the keys the ingest writes
    // and DiffCard reads. The `op`/`additions`/`deletions` this used to read
    // exist on ZERO spans, so the +N/−N chips never rendered once and every
    // `tool.Write` file creation was labelled "Update".
    diffOp: diffOpLabel(a.edit_op || ''),
    diffAdd: a.added_lines ?? null,
    diffDel: a.removed_lines ?? null,

    hasBashEdits: Boolean(bashEdits),
    bashEdits,

    hasTerm: term.has,
    termCmd: term.cmd,
    termLines: term.lines,

    hasRules: Boolean(rules),
    rules,

    hasDeny: Boolean(deny),
    deny,

    hasMcp: Boolean(mcp),
    mcp,

    hasChildren: children.length > 0,
    children: children.map(c => ({
      spanId: c.span_id,
      dot: barColor(c.name || ''),
      title: spanLabel(c),
      sub: c.name,
      meta: fmtDuration(c.duration_ms) || '',
    })),

    hasAttrs: attrKeys.length > 0,
    attrs: attrKeys.map(k => ({ k, v: a[k] })),

    empty: !hasProse && !ask && !deny && !mcp && !hasDiff && !term.has
      && !bashEdits && !rules && !children.length,
    // An empty body is not always an empty span. When the attributes
    // themselves account for content that exists but was not captured, saying
    // "metadata only" contradicts the very panel beside it.
    encrypted: hasEncryptedThinking(span),
    encryptedNote: [
      a.thinking_blocks ? `${a.thinking_blocks} block${a.thinking_blocks === 1 ? '' : 's'}` : '',
      a.thinking_signature_bytes ? `${a.thinking_signature_bytes} B signature` : '',
      span.output_tokens ? `${fmtTokens(span.output_tokens)} tokens` : '',
    ].filter(Boolean).join(' · '),
    plainProse: plainText(prose.text),
  }
}
