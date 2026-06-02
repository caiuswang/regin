import { ref, watch, onMounted, onBeforeUnmount } from 'vue'

// Two reading affordances for the live conversation trace, extracted here so the
// (already large) SessionConversationView SFC only wires them up:
//
//   1. PIN A SPAN — hold a chosen span at its on-screen Y across the 4s live
//      poll. The poll mutates the middle/bottom of the document (appends under
//      the active prompt, prunes retired placeholders, auto-folds the prior
//      prompt), so we anchor on the pinned ELEMENT's rect — capture its viewport
//      top before the DOM patch (flush:'pre'), re-measure after (flush:'post'),
//      and add the delta back to the scroller. Anchoring on the element (not on
//      scrollHeight, the way reverse-pagination does) means rows added BELOW the
//      pinned span don't shove it up.
//
//   2. FOLLOW TAIL — terminal-style auto-stick to the newest span: after every
//      poll, scroll to the bottom. A manual scroll-up turns it off; while
//      scrolled up we count new spans so the pill can show a "N new" hint.
//
// The two are mutually exclusive: enabling one clears the other, and the
// post-patch handler checks followTail first.
//
// Deps (so the composable owns no DOM knowledge of its own):
//   - spans:       getter for the reactive span list (the poll's mutation source)
//   - resolveEl:   spanId -> the row's DOM element (or null if folded/absent)
//   - getScroller: () -> the scroll container (.content-scroll)
//   - onPinExpand: spanId -> force-expand its owning prompt/agent so the pinned
//                  row can't be folded out of the DOM by the auto-fold watcher

// A span is pinnable only if it's a STABLE, resolved row. Transient placeholders
// (`promptlive-`/`pending-`/`permreq-`), still-PENDING blockers, and the
// permission gate get retired + replaced by a resolved span with a NEW span_id
// on the next poll (server reconcile, applied via dropRetiredSpans), so a pin
// keyed on their span_id would dangle. We simply don't offer the pin on them.
const PLACEHOLDER_PREFIXES = ['promptlive-', 'pending-', 'permreq-']

export function isPinnableSpan(span) {
  if (!span) return false
  if (span.status_code === 'PENDING') return false
  if (span.name === 'permission.request') return false
  const id = span.span_id || ''
  return !PLACEHOLDER_PREFIXES.some((p) => id.startsWith(p))
}

// Within this many px of the bottom counts as "at the bottom" — generous so a
// poll that grows the page by a few px doesn't read as the user scrolling up.
const AT_BOTTOM_PX = 80

export function useConversationPins({ spans, resolveEl, getScroller, onPinExpand }) {
  const pinnedSpanId = ref(null)
  const followTail = ref(false)
  const atBottom = ref(true)
  const newSinceScroll = ref(0)

  let anchorTop0 = null          // pinned element's viewport top, captured pre-patch
  let programmaticScroll = false // our own scrollTop writes must not read as user scroll
  let lastSpanCount = 0          // for the "N new while scrolled up" hint
  let prevScrollTop = 0
  // The "N new" badge means "things happened while you were scrolled away" — so
  // it must NOT count the initial lazy-load fill that lands while the reader is
  // still parked at the top of a long trace. Only start counting once the user
  // has actually scrolled.
  let userHasScrolled = false

  // Clear the programmatic flag on the next frame, after the scroll event our
  // write triggered has fired and been ignored.
  function releaseProgrammatic() {
    requestAnimationFrame(() => { programmaticScroll = false })
  }

  // Stick to the bottom. `retry` re-sticks over the next two frames: a large
  // trailing span often finishes laying out AFTER this tick (markdown/diff/
  // async-fetched content), growing scrollHeight without firing a scroll event
  // — a single write would then land short of the true bottom. The
  // programmatic latch is held across the retry frames so the re-sticks don't
  // read as a user scroll and cancel follow-tail.
  function scrollToBottom(scroller, { retry = false } = {}) {
    const s = scroller || getScroller()
    if (!s) return
    programmaticScroll = true
    s.scrollTop = s.scrollHeight
    if (!retry) { releaseProgrammatic(); return }
    requestAnimationFrame(() => {
      s.scrollTop = s.scrollHeight
      requestAnimationFrame(() => {
        s.scrollTop = s.scrollHeight
        releaseProgrammatic()
      })
    })
  }

  // Re-seat the pinned row at its captured viewport top. Returns true if it had
  // to move the scroller. Called repeatedly across a couple of frames after a
  // mutation because content above the pinned row (markdown/preview cards) often
  // finishes laying out AFTER the synchronous post-patch tick — a single
  // correction would then under-compensate.
  //
  // NOTE: in practice the browser's native scroll anchoring (the scroll
  // container is at the default `overflow-anchor: auto`) already holds the
  // pinned row in place, so `delta` is usually 0 and this is a no-op fallback.
  // It's the safety net for cases native anchoring suppresses (anchor node
  // removed/recreated by the reconcile). Don't set `overflow-anchor: none` on
  // the scroller without re-verifying pin hold — that hands the whole job here.
  function restorePinnedAnchor() {
    if (followTail.value || !pinnedSpanId.value || anchorTop0 == null) return false
    const el = resolveEl(pinnedSpanId.value)
    if (!el) return false
    const s = getScroller()
    if (!s) return false
    const delta = el.getBoundingClientRect().top - anchorTop0
    if (Math.abs(delta) < 1) return false
    programmaticScroll = true
    s.scrollTop += delta
    releaseProgrammatic()
    return true
  }

  function togglePin(spanId) {
    if (pinnedSpanId.value === spanId) { pinnedSpanId.value = null; return }
    pinnedSpanId.value = spanId
    followTail.value = false          // mutually exclusive
    if (onPinExpand) onPinExpand(spanId)
  }

  function enableFollow() {
    pinnedSpanId.value = null          // mutually exclusive
    followTail.value = true
    newSinceScroll.value = 0
    scrollToBottom()
  }

  function disableFollow() { followTail.value = false }

  // Native scroll anchoring (the scroller is at the default `overflow-anchor:
  // auto`) holds a top-anchor and nudges scrollTop *up* when content is appended
  // below. While following that backfires twice: it fights the stick-to-bottom,
  // and the upward nudge reads as a move-away-from-bottom, cancelling follow.
  // So disable anchoring on the scroller while following and restore it
  // otherwise. Pin and follow are mutually exclusive, so this never strands pin
  // (which DOES rely on native anchoring — see restorePinnedAnchor).
  watch(followTail, (on) => {
    const s = getScroller()
    if (s) s.style.overflowAnchor = on ? 'none' : ''
  })

  // Pre-patch: snapshot the pinned element's viewport top while the DOM still
  // reflects the OLD state.
  watch(spans, () => {
    if (!pinnedSpanId.value) { anchorTop0 = null; return }
    const el = resolveEl(pinnedSpanId.value)
    anchorTop0 = el ? el.getBoundingClientRect().top : null
  }, { flush: 'pre' })

  // Post-patch: follow the tail, or restore the pinned element to its old top.
  watch(spans, (curr) => {
    const count = (curr || []).length
    if (count > lastSpanCount && userHasScrolled && !followTail.value && !atBottom.value) {
      newSinceScroll.value += count - lastSpanCount
    }
    lastSpanCount = count

    const scroller = getScroller()
    if (!scroller) return
    if (followTail.value) { scrollToBottom(scroller, { retry: true }); return }
    if (!pinnedSpanId.value || anchorTop0 == null) return
    // Correct now, then again over the next two frames to absorb async layout
    // (markdown/preview cards above the pinned row settling late).
    restorePinnedAnchor()
    requestAnimationFrame(() => {
      restorePinnedAnchor()
      requestAnimationFrame(restorePinnedAnchor)
    })
  }, { flush: 'post' })

  function onScroll() {
    const s = getScroller()
    if (!s) return
    const dist = s.scrollHeight - s.scrollTop - s.clientHeight
    atBottom.value = dist <= AT_BOTTOM_PX
    if (atBottom.value) newSinceScroll.value = 0
    if (!programmaticScroll && s.scrollTop !== prevScrollTop) {
      userHasScrolled = true
      // Only a genuine upward user scroll *away from the bottom* cancels
      // follow-tail. While following you're always parked at/near the bottom,
      // so every browser-induced scrollTop change (a shrink-clamp when content
      // above shrinks, sub-pixel layout settling) keeps you at the bottom and
      // must NOT drop follow — without this guard a <2px jitter silently
      // disabled follow and the view stopped tracking the newest spans. Gating
      // on `!atBottom.value` (computed just above) is what distinguishes those
      // involuntary jitters from the user actually scrolling up to read back.
      // (Native scroll anchoring, the other nudger, is disabled while
      // following — see the followTail watch above.)
      if (followTail.value && !atBottom.value && s.scrollTop < prevScrollTop) {
        followTail.value = false
      }
    }
    prevScrollTop = s.scrollTop
  }

  onMounted(() => {
    const s = getScroller()
    if (s) {
      prevScrollTop = s.scrollTop
      s.addEventListener('scroll', onScroll, { passive: true })
    }
    lastSpanCount = (spans() || []).length
    onScroll()
  })

  onBeforeUnmount(() => {
    const s = getScroller()
    if (s) {
      s.removeEventListener('scroll', onScroll)
      // The scroller is owned by the parent layout and outlives this view —
      // don't leave it stuck at `overflow-anchor: none` for the next view.
      s.style.overflowAnchor = ''
    }
  })

  return {
    pinnedSpanId, followTail, atBottom, newSinceScroll,
    isPinnable: isPinnableSpan, togglePin, enableFollow, disableFollow,
  }
}
