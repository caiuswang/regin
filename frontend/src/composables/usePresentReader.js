import { ref, computed, watch } from 'vue'
import { spanLabel } from '../utils/traceFormatters.js'
import { spanHasBody } from '../utils/presentModel.js'

const ONLY_BODY_KEY = 'regin.present.onlyBody'

// A blocking call is served as `<prefix><tool_use_id[:13]>` while it runs and
// retired the moment the resolved row lands (lib/trace/pending_spans.py). The
// id IS the handoff key, so the successor of a placeholder that vanished
// mid-read is findable without a clock or a guess.
const PENDING_PREFIXES = ['pending-', 'permreq-']
// A live prompt is served as `promptlive-<sha1(session + text[:512])>` until
// the transcript scan lands its real `prompt-<uuid>` anchor. The key is the
// TEXT, not an id the client can recompute, so the heir is found the way the
// server pairs them (lib/trace/pending_spans.py::prompt_placeholder_id).
const PROMPT_PLACEHOLDER_PREFIX = 'promptlive-'
// A byte-capped anchor carries this suffix; the untruncated text stays on the
// placeholder (`_PROMPT_ANCHOR_TRUNC_MARKER` in lib/trace/merge.py).
const PROMPT_TRUNC_MARKER = '\n…[truncated]'

const promptText = (span) => {
  const t = span?.attributes?.text
  return typeof t === 'string' ? t : ''
}

// One prompt's text is the other's prefix. Covers all three shapes the merge
// pairs: identical text, a truncated anchor, and a slash command whose anchor
// holds only the collapsed echo the placeholder's expansion starts with.
function textPairs(a, b) {
  const trim = t => (t.endsWith(PROMPT_TRUNC_MARKER) ? t.slice(0, -PROMPT_TRUNC_MARKER.length) : t)
  const x = trim(a)
  const y = trim(b)
  if (!x || !y) return false
  return x.startsWith(y) || y.startsWith(x)
}

function promptHeirIn(seq, entry, known) {
  const text = promptText(entry?.span)
  if (!text) return null
  const hits = seq.filter(e => (
    e.span?.name === 'prompt'
    && !e.id.startsWith(PROMPT_PLACEHOLDER_PREFIX)
    && textPairs(text, promptText(e.span))
  ))
  // The heir is the row that just took the placeholder's slot. Preferring the
  // arrivals rules out an older prompt whose collapsed slash echo happens to
  // prefix this one, and an ambiguous key binds nothing: showing the wrong
  // prompt is worse than holding the copy the reader already has.
  const fresh = hits.filter(e => !known.has(e.id))
  const pool = fresh.length ? fresh : hits
  return pool.length === 1 ? pool[0] : null
}

function successorIn(seq, entry, known) {
  const id = entry?.id || ''
  if (id.startsWith(PROMPT_PLACEHOLDER_PREFIX)) return promptHeirIn(seq, entry, known)
  const prefix = PENDING_PREFIXES.find(p => id.startsWith(p))
  if (!prefix) return null
  const toolUseId = id.slice(prefix.length)
  if (!toolUseId) return null
  return seq.find(e => (e.span?.attributes?.tool_use_id || '').startsWith(toolUseId)) || null
}

function readOnlyBody() {
  // Default ON: in a long trace most spans are bookkeeping, so an unfiltered
  // walk is mostly noise. Stored as an explicit '0' so only a deliberate
  // opt-out survives a reload.
  try { return localStorage.getItem(ONLY_BODY_KEY) !== '0' } catch { return true }
}

// Presentation-mode reader state: ONE field plus three moves.
//
// Deliberately not derived from the detail rail — opening does not require the
// rail to be open, and closing the rail does not close the reader. `presentId`
// is also the shape a `?span=…&read=1` permalink would mirror, if one lands.
//
// `entries` is the trace-ordered structure the conversation feed builds
// (useSpanTree): one entry per turn, each carrying its prompt and its
// flattened descendants with subagent children already inline after their
// parent. Walking it — rather than the feed's rendered rows — means the reader
// steps through folded agents and Focus-hidden spans too, which is the point.
export function usePresentReader(entries, { onSelect } = {}) {
  const presentId = ref(null)
  // The open span, held independently of the walk — the reader's own copy, not
  // a lookup. Two things put a span out of the walk's reach and neither may
  // close a reader that is already showing it:
  //   * it was never in it — a scoped feed's SYNTHESIZED task prompt has no row
  //     in the session's span list (opens as a one-span reader, steps greyed);
  //   * it LEFT it — the live poll prunes what the serve-time merge retired
  //     (a resolved `pending-`/`permreq-` call, a promoted `promptlive-`, a
  //     blocker `_drop_stale_blockers` dropped because a newer prompt landed).
  // Without the second case, sending a follow-up retired every still-pending
  // span of the turn you were reading and the reader vanished under you.
  const held = ref(null)
  // The walk slot the open span was landed at, as `{ id }` — the id of the
  // entry it sat behind, or null when it was the first. Absent (`null` anchor)
  // when the span was never on the walk at all. A span that LEFT the walk with
  // no heir would otherwise strand the reader: its own index is gone, so both
  // steps go dead and the only way back to the newest spans is to close and
  // reopen. The predecessor is older and already resolved, so it survives the
  // retirement that took the open span with it.
  const anchor = ref(null)
  // Bumped only by a DELIBERATE move (open / ↑↓ / child click), never by the
  // live-poll rebind below. The reader resets its scroll on this, not on the
  // span id: a placeholder retiring into its resolved twin changes the id
  // while the document stays the same one you are mid-way through reading,
  // and resetting there threw away the position the whole held/anchor
  // machinery exists to preserve.
  const navSeq = ref(0)
  const onlyBody = ref(readOnlyBody())

  const spanSeq = computed(() => {
    const out = []
    const seen = new Set()
    const parents = new Set()
    const push = (span) => {
      if (!span?.span_id || seen.has(span.span_id)) return
      seen.add(span.span_id)
      if (span.parent_id) parents.add(span.parent_id)
      out.push({ id: span.span_id, label: spanLabel(span), span, seq: out.length })
    }
    for (const entry of entries.value || []) {
      push(entry.type === 'group' ? entry.prompt : entry.span)
      for (const d of entry.descendants || []) push(d.span)
    }
    // Second pass: `hasBody` for a subagent depends on whether any of its
    // children reached the sequence, which is only knowable once it is built.
    for (const e of out) e.hasBody = spanHasBody(e.span, parents.has(e.id))
    return out
  })

  // The "content only" walk filter (§11b). It changes only what ↑↓ LANDS on —
  // never what a given span shows, and never whether one can be opened
  // directly.
  const readable = computed(() => (
    onlyBody.value ? spanSeq.value.filter(e => e.hasBody) : spanSeq.value
  ))
  const skipped = computed(() => spanSeq.value.length - readable.value.length)

  const index = computed(() =>
    spanSeq.value.findIndex(s => s.id === presentId.value))
  const readableIndex = computed(() =>
    readable.value.findIndex(s => s.id === presentId.value))

  // The live entry wins whenever the walk still has it, so a span that is
  // still being written keeps growing in the reader; `held` is the floor, not
  // a cache.
  const current = computed(() => {
    // Short-circuit on a closed reader BEFORE touching `index`, which forces
    // `spanSeq` and, through it, a whole second span-tree walk. The reader is
    // mounted for the life of the view, so every consumer of `isOpen` was
    // paying for that walk on every live poll with nothing on screen.
    if (!presentId.value) return null
    if (index.value >= 0) return spanSeq.value[index.value]
    const h = held.value
    return h && h.id === presentId.value ? h : null
  })

  // Openness is derived from what actually RESOLVES, never from the raw id: an
  // id with nothing behind it is closed, whatever it is set to.
  const isOpen = computed(() => current.value != null)

  // Open but not on the readable walk — filtered out by "content only", or
  // retired out of the walk entirely. Either way the position counter has no
  // slot to name, and the footer says so rather than reading 0.
  const offList = computed(() => isOpen.value && readableIndex.value < 0)

  // Where the walk resumes for a span that has LEFT it: from the recorded
  // anchor's slot, so ↓ reaches everything that landed after — including the
  // spans written since, which is the whole point of reading a live session.
  function resumed(delta) {
    const a = anchor.value
    if (!a) return null
    const here = a.id ? spanSeq.value.findIndex(s => s.id === a.id) : -1
    if (a.id && here < 0) return null
    return delta > 0
      ? (readable.value.find(e => e.seq > here && e.id !== presentId.value) || null)
      : (readable.value.filter(e => e.seq <= here).pop() || null)
  }

  // One function behind both the footer labels and the move, so the control can
  // never name a neighbour the keypress does not go to. From an off-list span
  // the walk RESUMES: the nearest readable entry on that side of where this
  // span sits in the full order.
  function neighbour(delta) {
    if (index.value < 0) return resumed(delta)
    if (readableIndex.value >= 0) return readable.value[readableIndex.value + delta] || null
    const here = index.value
    return delta > 0
      ? (readable.value.find(e => e.seq > here) || null)
      : (readable.value.filter(e => e.seq < here).pop() || null)
  }
  const prev = computed(() => neighbour(-1))
  const next = computed(() => neighbour(1))

  function land(entry) {
    const at = spanSeq.value.findIndex(s => s.id === entry.id)
    anchor.value = { id: at > 0 ? spanSeq.value[at - 1].id : null }
    presentId.value = entry.id
    held.value = entry
    onSelect?.(entry.span)
  }

  // A retired span re-binds to its successor where the handoff key names one
  // (a resolved tool call, a promoted prompt anchor), so the reader keeps
  // reading the same document — now resolved, and back on the walk with both
  // steps live. Where it names none, or names more than one, the held copy
  // stands and `anchor` keeps the walk reachable from where it sat.
  watch(spanSeq, (seq, prevSeq) => {
    if (!presentId.value || index.value >= 0) return
    const heir = successorIn(seq, held.value, new Set((prevSeq || []).map(e => e.id)))
    if (heir) land(heir)
  })

  function openPresent(spanOrId) {
    const span = typeof spanOrId === 'string' ? null : spanOrId
    const id = span ? span.span_id : spanOrId
    if (!id) return
    navSeq.value++
    const entry = spanSeq.value.find(s => s.id === id)
    if (entry) { land(entry); return }
    // Filtering never blocks an explicit request, and neither does absence from
    // the walk — but an id with no span behind it closes silently rather than
    // rendering an empty shell.
    if (!span) return
    // Never on the walk, so there is no slot to resume from: the steps stay
    // greyed, which is what a one-span reader promises.
    anchor.value = null
    held.value = { id, label: spanLabel(span), span }
    presentId.value = id
    onSelect?.(span)
  }

  // Clamps at both ends rather than wrapping: the footer greys its control at
  // the boundary, so a no-op there is what the affordance already promised.
  function movePresent(delta) {
    const target = neighbour(delta)
    if (target) { navSeq.value++; land(target) }
  }

  function closePresent() {
    presentId.value = null
    held.value = null
    anchor.value = null
  }

  function toggleOnlyBody() {
    onlyBody.value = !onlyBody.value
    try { localStorage.setItem(ONLY_BODY_KEY, onlyBody.value ? '1' : '0') } catch { /* private mode */ }
  }

  return {
    presentId, isOpen, spanSeq, index, current, prev, next, navSeq,
    onlyBody, readable, readableIndex, skipped, offList, toggleOnlyBody,
    openPresent, movePresent, closePresent,
  }
}
