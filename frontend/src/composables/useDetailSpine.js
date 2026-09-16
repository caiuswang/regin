import { computed } from 'vue'
import { runStateOf, isLiveRunState } from '../utils/liveRows.js'
import { tierOf, keptByFocus, didFail, TIER_FULL, TIER_CONDENSED, TIER_FOLDED } from '../components/conversation/dispatch.js'
import { useFocusMode } from './useFocusMode.js'

// Everything the three detail modes do to a turn's spine, in one place: which
// rows render, which of them a strip sets aside, which turns the mode forces
// open, and which span a turn carries down from the one above it.
//
// It lives outside SessionConversationView because it is a self-contained
// model — pure over (rows, mode, open strips) — and the feed component is a
// DOM orchestrator that was already at its complexity ceiling. The feed keeps
// the parts that touch the DOM (refs, scroll, fetch emits); the shape of the
// spine is decided here.

function fullSpine(tiered) {
  return { rows: tiered.map((r) => ({ ...r, tier: TIER_FULL })), setAside: false }
}

// A subagent's card is the parent's whole account of that agent — its goal,
// its result, the "Agent view →" that opens its own trace — so it is never
// set aside behind a strip in any mode. Fold the turn and it goes with the
// turn; open the turn and it is there whether the strip is open or not.
function isAgentCard(row) {
  return row.span.name === 'subagent.start'
}

// Condensed and Focus differ only in WHICH rows they set aside; the shape they
// build is identical, so they share one builder. Keeping them as two functions
// meant every fix to the anchor, the reveal tier or the empty case had to be
// made twice, and Focus's copy is where the "newest span" bug lived.
//
// One strip per turn, anchored at the FIRST set-aside span, and a revealed row
// comes back at its own position on the turn's own spine — flat, with no
// wrapper: a boxed group re-reads as a nested sub-trace, and it would need its
// own `minmax(0, 1fr)` width constraint or the nowrap subject lines size the
// column to max-content and overflow the feed.
//
// `revealTier` is what the two modes disagree about. Condensed dims a revealed
// row to a one-liner, because promoting the noise it folded would let one click
// quietly undo the mode. Focus CANNOT do that: its strip holds the whole turn,
// so dimming everything rendered a wall of grey one-liners — and a
// `subagent.start` demoted to a one-liner loses the chevron that folds it,
// stranding the reader inside an agent they can no longer close.
function stripSpine(turnId, tiered, open, kind, isKept, revealTier) {
  const aside = tiered.filter((r) => !isKept(r))
  if (!aside.length) return { rows: tiered, setAside: false }
  const spans = aside.map((r) => r.span)
  const anchorId = spans[0].span_id
  const out = []
  for (const row of tiered) {
    if (row.span.span_id === anchorId) {
      out.push({ strip: true, kind, turnId, foldedSpans: spans })
    }
    if (isKept(row)) out.push(row)
    else if (open) out.push({ ...row, tier: revealTier(row) })
  }
  return { rows: out, setAside: true }
}

// Which of a turn's failures the agent went on FROM. A failing tool call is
// only the turn's story when the turn ended on it; if any real work follows —
// a retry, a read, a reply, at any tier — the agent recovered, and what the
// reader wants is what it recovered INTO.
//
// This is the split Sentry draws between a handled and an unhandled exception:
// a caught-and-recovered error is a breadcrumb, not the issue title. Without
// it the last failure of a turn always outranked the answer that came before
// it, because `keptByFocus` keeps every failure and the ladder takes the last
// of the rung. Every frequent `tool.failure` in the store is the model
// fumbling and immediately correcting — `File does not exist`, `(eval):1: no
// such file or directory: .venv/bin/python`, `(eval):1: == not found` — and
// over 9,119 real turns this moves 96 headlines, each from a failure or a
// misread file onto the turn's own reply, edit or question.
//
// A later failure is not a recovery: two fumbles in a row leave the second one
// standing, which is why the scan tracks non-failed work only.
function recoveredFailures(tiered) {
  const ids = new Set()
  let workAfter = false
  for (let i = tiered.length - 1; i >= 0; i -= 1) {
    const row = tiered[i]
    if (didFail(row.span)) {
      if (workAfter) ids.add(row.span.span_id)
    } else if (row.tier !== TIER_FOLDED) {
      workAfter = true
    }
  }
  return ids
}

// The first of Focus's two cards: the turn's newest USEFUL span, with
// everything else — edits, shell runs, a whole subagent's run — behind the
// turn's strip. The second is `latestWorkOf`, and a turn with work still in
// flight also keeps its running rows (see `focusSpine`).
//
// "Newest" alone was the original rule and it was measurably wrong: over 254
// turns of the live store the last renderable span was a folded-tier one in 47
// of them (`harness.recap` x18, `assistant.thinking` x14,
// `harness.local_command` x13, `rule.check` x2). "Useful" is what fixes that,
// and it is a LADDER, not a single test — every rung is a last-resort for the
// one above, so the mode always lands on the most meaningful thing the turn
// actually has:
//
//   1. `keptByFocus` — the turn's outcome, a world-change, a failure, an
//      agent's result. This is the answer on almost every real turn.
//   2. the turn's own non-folded work (a shell run, a read) — a turn that only
//      searched has no outcome span, but it still did something.
//   3. any non-folded row, including one inside a subagent.
//   4. anything at all.
//
// Rung 4 is why a `harness.recap` can still headline an all-recap turn:
// "always show one card" and "never headline a folded span" cannot both hold
// there, and a bare strip is indistinguishable from an empty turn. Taking the
// LAST of the winning rung is what keeps "newest" true within "useful".
//
// Rung 1 additionally drops any failure the turn RECOVERED from — see
// `recoveredFailures`. Only rung 1: if a demoted failure is all the turn has,
// the lower rungs still find it, so the mode never trades one card for none.
function focusHeadline(tiered) {
  const recovered = recoveredFailures(tiered)
  const rungs = [
    tiered.filter((r) => keptByFocus(r) && !recovered.has(r.span.span_id)),
    tiered.filter((r) => r.tier !== TIER_FOLDED && !r.inAgent),
    tiered.filter((r) => r.tier !== TIER_FOLDED),
    tiered,
  ]
  const pick = rungs.find((rung) => rung.length)
  return pick[pick.length - 1].span.span_id
}

// The one span a COLLAPSED turn is still worth showing: the same "newest
// useful" pick Focus headlines with. Folding a turn must not cost the reader
// its outcome, and the positional last row was a `harness.recap` on 18 of 254
// real turns — so a folded turn used to advertise itself with its own noise.
export function usefulTailOf(rows) {
  if (!rows.length) return null
  const tiered = rows.map((r) => ({ ...r, tier: tierOf(r.span) }))
  const id = focusHeadline(tiered)
  return tiered.find((r) => r.span.span_id === id)?.span || null
}

// The turn's newest WORK, which is a different question from its newest
// USEFUL span and has to be asked separately. The headline ladder answers
// "what did this turn produce", and a reply outranks a `tool.Bash` on every
// rung of it — so on a turn that answered and then went back to the shell, the
// run the reader came to watch sat behind the strip with nothing on the spine
// naming it. The strip advertises its mix, not what the agent is doing now.
//
// Strictly the NEWEST such row: the scan stops at the first one it finds and
// never keeps looking past it. Skipping a row and carrying on backwards would
// answer with an OLDER span, which is the one thing this question must not do
// — a turn that edited a file and then launched an agent would spend its
// second card on the edit while the agent, the newest thing in the turn, is
// what the reader is watching.
//
// Tier 3 and chrome are stepped over rather than stopped at: tier 3 is where
// the last row of roughly a quarter of real turns lands (`harness.recap`,
// `assistant.thinking`, `harness.local_command`, `rule.check`), and a recap as
// a turn's second card is the noise the mode exists to remove.
//
// An `inAgent` answer is reported as none. A subagent is one unit, and its
// newest step is already accounted for on the spine by the agent's own card —
// flattening the step out beside it would make a child's shell run
// indistinguishable from work the parent did. This is also what keeps a
// failure the turn RECOVERED from off the spine when the recovery happened
// inside an agent: `recoveredFailures` counts an agent's step as the turn
// moving on, so the fumble is demoted, and stopping here means nothing scans
// back to it.
function latestWorkOf(tiered) {
  for (let i = tiered.length - 1; i >= 0; i -= 1) {
    const row = tiered[i]
    if (row.tier !== TIER_FULL && row.tier !== TIER_CONDENSED) continue
    return row.inAgent ? null : row.span.span_id
  }
  return null
}

function focusSpine(turnId, tiered, open) {
  const headlineId = focusHeadline(tiered)
  const latestWorkId = latestWorkOf(tiered)
  // The one card carries its body whatever it would otherwise tier as — that
  // is the difference between Focus and Condensed's one-liners.
  const promoted = tiered.map((r) => (r.span.span_id === headlineId ? { ...r, tier: TIER_FULL } : r))
  // A span still in flight is kept BESIDE the headline, not instead of it.
  // `tierOf` already promotes a running span to TIER_FULL on the grounds that
  // it is the one row the reader came for, and Condensed honours that because
  // its keep-set reads the tier — Focus's reads only the headline id, so the
  // promotion did nothing here. Whenever the headline was some other row (a
  // completed edit or failure that landed after the run started, or the run
  // being `inAgent`, which `keptByFocus` never keeps) the only moving thing on
  // the page sat behind the strip, and a strip advertises its mix, not its
  // liveness: the reader could not tell what the agent was doing without
  // opening it.
  //
  // `runState` is the ROW's — stamped by `buildSpine` from the view's own
  // reading, which for a subagent card is the roster's verdict on the agent
  // rather than the marker span's own status (a marker is never PENDING).
  return stripSpine(turnId, promoted, open, 'latest',
    (r) => r.span.span_id === headlineId || r.span.span_id === latestWorkId
      || isLiveRunState(r.runState) || isAgentCard(r),
    (r) => r.tier)
}

// One enum, three branches, and the same two invariants in all of them: no
// span ever leaves the feed, and a set-aside span comes back at its OWN
// position on the spine rather than piling up at the end of the turn.
//
// Every row leaves here carrying `tier` and `runState`, stamped ONCE, so the
// template, the keep-set and the turn's liveness all read the same answer.
// `runStateFor` is the view's reading of a span (defaults to the span's own
// PENDING state); `live` is derived from the same stamped rows the spine
// renders, so a turn cannot be live for the header and finished for the feed.
export function buildSpine(mode, turnId, rows, isOpen, runStateFor = runStateOf) {
  if (!rows.length) return { rows: [], setAside: false, live: false }
  const tiered = rows.map((r) => ({ ...r, tier: tierOf(r.span), runState: runStateFor(r.span) }))
  const live = tiered.some((r) => isLiveRunState(r.runState))
  if (mode === 'latest') {
    return { ...focusSpine(turnId, tiered, isOpen(turnId, 'latest')), live }
  }
  if (mode === 'condensed') {
    return {
      ...stripSpine(turnId, tiered, isOpen(turnId, 'fold'), 'fold',
        (r) => r.tier !== TIER_FOLDED || isAgentCard(r), () => TIER_FOLDED),
      live,
    }
  }
  return { ...fullSpine(tiered), live }
}

// `getRowsFor(turnId)` returns the turn's renderable rows; `isPromptExpanded`
// is the feed's own hand-expand state, which the mode can override but never
// replaces. `runStateFor(span)` is the view's reading of a span's liveness.
export function useDetailSpine({
  getEntries, getRowsFor, isPromptExpanded, isPromptCollapsed, runStateFor = runStateOf,
}) {
  const { condensedOn, latestOn, isFoldOpen, toggleFold } = useFocusMode()

  const mode = computed(() => {
    if (latestOn.value) return 'latest'
    return condensedOn.value ? 'condensed' : 'full'
  })

  // Memoized per turn for the same reason `renderableByPrompt` is: the
  // template asks for this several times per turn (the header, the spine's
  // `v-if`, its `v-for`, and a tier read per row), and recomputing it per call
  // re-runs `tierOf` — and therefore `rendersInConversation` — a few hundred
  // times per turn on every live-poll re-render. The tier is stamped onto each
  // row here so the template never calls `tierOf` at all.
  const spineByTurn = computed(() => {
    const byId = new Map()
    for (const entry of getEntries()) {
      if (!entry.prompt) continue
      const turnId = entry.prompt.span_id
      byId.set(turnId, buildSpine(mode.value, turnId, getRowsFor(turnId), isFoldOpen, runStateFor))
    }
    return byId
  })

  // Focus and `live` are both DEFAULTS, not locks. Focus used to be a lock —
  // defensible while a Focus turn was one span by construction, since there was
  // nothing to collapse it to. Now that the mode keeps a turn's whole keep-set
  // the turn has a real closed state, and a default with no closed state is a
  // toggle that silently does nothing.
  //
  // Dropping the lock is also what lets `gutterAction` be one answer instead of
  // three: with Focus foldable like every other mode, the gutter always folds
  // the turn and the strip owns its own disclosure.
  function isTurnOpenByDefault(turnId) {
    return latestOn.value || !!spineByTurn.value.get(turnId)?.live
  }
  function isTurnExpanded(turnId) {
    if (isPromptCollapsed(turnId)) return false
    return isTurnOpenByDefault(turnId) || isPromptExpanded(turnId)
  }

  // A folded turn still shows what it produced: its useful tail, rendered under
  // its OWN chips. Nothing else on the page is consulted.
  //
  // This used to be relative — turn N's tail rendered above turn N+1's prompt,
  // as the thing that prompt is answering — with a special case for the last
  // turn, which has no N+1. Both halves break on a LIVE session, and quietly:
  // `merge_spans` keeps a PENDING `promptlive-` placeholder beside every real
  // prompt while a turn is still being written, so the trailing group is the
  // placeholder rather than the turn, and a folded turn handed its tail to a
  // group that renders no card. It looked correct on every settled session and
  // every fixture, because a newer prompt makes the placeholder stale and merge
  // drops it — which is exactly why "which turn is last" is not a question this
  // view can ask.
  //
  // Keyed by the turn that PRODUCED the span, the question never comes up.
  const ownTailByTurn = computed(() => {
    const byId = new Map()
    for (const entry of getEntries()) {
      if (!entry.prompt || isTurnExpanded(entry.prompt.span_id)) continue
      const tail = usefulTailOf(getRowsFor(entry.prompt.span_id))
      // Still a REFERENCE, never a copy — the span keeps its own id, so counts,
      // exports and keyboard walks see it exactly once.
      if (tail) byId.set(entry.prompt.span_id, tail)
    }
    return byId
  })

  // The strip key this mode folds on, open or closed.
  const stripKind = () => (latestOn.value ? 'latest' : 'fold')

  // What this turn's gutter control does, or '' when it has none. Called from
  // inside the spine container, so "the turn is expanded and has rows" is
  // already true by construction.
  //
  // ONE meaning, in every mode and every state: fold this turn back to its
  // chips. It has worn three ('turn' / 'strip' / 'reveal'), and each variant
  // cost the reader the same way — a control whose meaning depends on state
  // they cannot see from the rail. The last of them silently swapped "fold the
  // turn" for "close the strip" whenever a strip happened to be open, so
  // clicking it left the turn open and looked like a dead button.
  //
  // The deep-run escape that motivated the 'strip' variant still works, better:
  // folding the turn from deep inside a revealed run collapses the whole thing
  // and anchors the reader on its prompt card. The strip keeps its own ▸/▾ for
  // reopening.
  //
  // Liveness is not consulted: folding a running turn is a real state.
  function gutterAction(turnId) {
    return spineByTurn.value.get(turnId) ? 'turn' : ''
  }

  // Is this span one Focus keeps on the spine, rather than one behind the
  // strip? The reveal path asks so it does not spring a turn's whole strip open
  // for a row that was already visible.
  function isKeptRow(turnId, spanId) {
    const built = spineByTurn.value.get(turnId)
    if (!built) return false
    return built.rows.some((r) => !r.strip && r.span.span_id === spanId
      && r.tier !== TIER_FOLDED)
  }

  // What the prompt card says about a turn. No numeral here describes spans
  // the mode set aside — an exact tally reads as an error budget and invites
  // counting instead of reading — but whether anything IS set aside is a
  // different question, and the reader needs the answer to it.
  function turnMeta(turnId) {
    return {
      setAside: !!spineByTurn.value.get(turnId)?.setAside,
      latest: latestOn.value,
      foldOpen: isFoldOpen(turnId, latestOn.value ? 'latest' : 'fold'),
      expanded: isTurnExpanded(turnId),
    }
  }

  // Every turn the mode opened without the reader asking — the set whose
  // subtrees and lazily-served bodies nobody else will fetch. A turn the reader
  // has since collapsed is not on screen and does not need either.
  const forcedOpenTurnIds = computed(() => getEntries()
    .filter((e) => e.type === 'group' && e.prompt
      && isTurnOpenByDefault(e.prompt.span_id) && isTurnExpanded(e.prompt.span_id))
    .map((e) => e.prompt.span_id))

  return {
    mode, condensedOn, latestOn, isFoldOpen, toggleFold,
    spineByTurn, ownTailByTurn, forcedOpenTurnIds,
    isTurnExpanded, isKeptRow, turnMeta,
    gutterAction, stripKind,
    rowsFor: (turnId) => spineByTurn.value.get(turnId)?.rows || [],
    stripKey: (turnId, kind) => `${turnId}:${kind}`,
  }
}
