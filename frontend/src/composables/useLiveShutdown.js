import api from '../api'

// Closing or deleting a session in the list is a trace-store write that says
// nothing to the process behind it: a still-running agent keeps emitting
// spans and reappears as a new partial trace. Probe which tier still holds
// the row so the confirmation can say so, then end that process before the
// row is settled.

const TIER_NOUN = {
  sdk: 'a regin-launched agent run',
  tmux: 'a live claude in a tmux pane',
}

// What regin will actually do to each tier, spelled out because for tmux it
// is a keystroke into a terminal the operator owns.
const TIER_ACTION = {
  sdk: 'regin will stop the run first — it finishes the turn in flight, emits '
    + 'SessionEnd and disconnects.',
  tmux: 'regin will cancel the turn in flight and then send /exit to END the '
    + 'claude process in that terminal.',
}

// Every one of these requests shells out to tmux on the server, and a
// shutdown holds its request for the pane-settle pause, the composer reset,
// the delivery ack and then the watch for the process to actually exit —
// seconds, not milliseconds. Selecting a few dozen rows would otherwise fire
// them all at once and leave the confirm dialog closed until the slowest
// returns.
const MAX_IN_FLIGHT = 4

async function pooled(items, fn) {
  const results = []
  for (let i = 0; i < items.length; i += MAX_IN_FLIGHT) {
    results.push(...await Promise.all(items.slice(i, i + MAX_IN_FLIGHT).map(fn)))
  }
  return results
}

// The server could not establish anything: a pane is registered that it could
// not read, so the session may still be running and nothing has been touched.
//
// Deliberately does NOT promise a stop. `graceful_close` returns early on a
// state whose `live` is False, and an undetermined state is one — so if the
// server's second probe fails the same way, nothing is attempted at all. The
// re-probe is the only reason to say "may".
const UNDETERMINED_ACTION = 'regin could not read the pane registered for this '
  + 'session, so it may not be able to stop it — the agent may keep running '
  + 'after the row is settled.'

export function useLiveShutdown() {
  // A probe that fails must not block the action it was only meant to
  // describe, so a request that threw reads as "nothing reachable here".
  //
  // An *undetermined* answer is kept rather than filtered out, even though its
  // `tier` is null. That null means "regin could not establish anything", not
  // "nothing is there" — dropping it here is what settled a possibly-running
  // agent with no warning and no shutdown attempt.
  async function probe(traceId) {
    try {
      const state = await api.get(`/sessions/${traceId}/live-state`)
      if (!state) return null
      if (state.undetermined) return { traceId, ...state }
      return state.tier && state.live ? { traceId, ...state } : null
    } catch {
      return null
    }
  }

  async function probeAll(traceIds) {
    return (await pooled(traceIds, probe)).filter(Boolean)
  }

  function singleWarning(state) {
    if (!state) return ''
    if (state.undetermined) {
      return '⚠️  regin could not tell whether this session is still running.\n\n'
        + `${UNDETERMINED_ACTION}\n\n`
    }
    return `⚠️  This session is still LIVE — ${TIER_NOUN[state.tier]}.\n\n`
      + `${TIER_ACTION[state.tier]}\n\n`
  }

  function batchWarning(states) {
    if (!states.length) return ''
    const live = states.filter(s => !s.undetermined)
    const unread = states.length - live.length
    const parts = []
    if (live.length) {
      const tiers = [...new Set(live.map(s => s.tier))]
      parts.push(`${live.length} ${live.length === 1 ? 'is' : 'are'} still LIVE. `
        + tiers.map(t => TIER_ACTION[t]).join(' '))
    }
    // Counted separately because it is a different claim: not "this is running
    // and regin will stop it" but "regin does not know, and may not stop it".
    if (unread) {
      parts.push(`${unread} could not be read — a registered pane regin could `
        + `not query, so ${unread === 1 ? 'it' : 'they'} may still be running.`)
    }
    const noun = `session${states.length === 1 ? '' : 's'}`
    return `⚠️  Of the selected ${noun}: ${parts.join(' ')}\n\n`
  }

  // Resolves to the traces regin could not stop, so the caller can warn and
  // still settle the row — an unreachable session is exactly the interrupted
  // one a manual close exists for. Each failure carries the server's answer
  // so the warning can separate "never touched your pane" from "cancelled
  // the turn but could not quit".
  async function shutdownAll(states) {
    const results = await pooled(states, async (state) => {
      try {
        const res = await api.post(`/sessions/${state.traceId}/shutdown`)
        // A null tier means that by the time the server looked, nothing was
        // holding the session any more — it ended between the probe that
        // worded the dialog and the click. Nothing failed, so warning about
        // it would raise a false alarm on a session that closed cleanly.
        // Unless it is `undetermined`: there the registry named a pane regin
        // could not read, so nothing was established and nothing was touched.
        const settled = res && (res.closed || (res.tier === null && !res.undetermined))
        return settled ? null : { ...state, failure: res }
      } catch {
        return { ...state, failure: null }
      }
    })
    return results.filter(Boolean)
  }

  // The sentence for a shutdown that did not take. A pane whose turn regin
  // cancelled has been changed even though it is still running, so saying
  // only "could not stop it" would understate what happened. For a single
  // row the server's own detail is carried through verbatim: it separates
  // "never touched your pane" from "/exit was submitted and the session
  // ignored it", which no count can express.
  function failureWarning(failed) {
    const noun = `live session${failed.length === 1 ? '' : 's'}`
    const detail = failed.length === 1 && failed[0].failure && failed[0].failure.detail
    if (detail) return `Could not stop this ${noun}: ${detail}; settling the trace anyway`
    const touched = failed.filter(f => f.failure && f.failure.interrupted).length
    const tail = touched
      ? ` — ${touched} had the turn in flight cancelled but stayed open`
      : ''
    return `Could not stop ${failed.length} ${noun}${tail}; settling the trace anyway`
  }

  return { probe, probeAll, singleWarning, batchWarning, shutdownAll, failureWarning }
}
