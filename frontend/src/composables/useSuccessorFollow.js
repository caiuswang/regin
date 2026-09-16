import { watch, onMounted, onUnmounted } from 'vue'
import api from '../api'
import { onSessionSuccessor } from './useSuccessorEvents'
import { useSuccessorJump } from './useSuccessorJump'

// Follow a `/clear` onto the conversation that replaced this one.
//
// Both session views need the identical three legs, so they live here rather
// than twice: the pushed frame (the primary signal — the heir's rows land
// under a trace neither view polls, so nothing else can discover it), a
// re-read when the stream reconnects and a frame may have been delivered to
// nobody, and a watcher on the summary's own field for whichever leg lands
// first.
//
// `currentId()` is the id the SUMMARY describes, never `route.params.id`:
// `jumpTo` refuses to move a page that is not showing `from`, and handing it
// the route's own param would make that guard a tautology.
export function useSuccessorFollow(currentId, successorOf) {
  const { jumpTo } = useSuccessorJump()
  let detach = null

  async function onFrame(frame) {
    if (frame) { jumpTo(frame.trace_id, frame.successor_trace_id); return }
    // Null frame = the stream reconnected. The poll cannot cover this: a
    // `/clear` ends the session, and both views stop polling an ended one, so
    // the page would sit on a dead id forever. Ask the server once.
    const id = currentId()
    if (!id) return
    let summary = null
    try {
      summary = await api.get(`/sessions/${id}/map?shallow=1&limit=1`)
    } catch { return }
    if (summary?.successor_trace_id) jumpTo(id, summary.successor_trace_id)
  }

  // `had === undefined` is the first summary resolving, not a succession that
  // happened while the operator was here. Opening a cleared session's page
  // deliberately — to read a trace whose heir is still running — must not
  // bounce you off it, and a `replace` would leave no history to come back to.
  watch(successorOf, (heir, had) => {
    if (had === undefined || !heir) return
    jumpTo(currentId(), heir)
  })

  onMounted(() => { detach = onSessionSuccessor(onFrame) })
  onUnmounted(() => { detach?.(); detach = null })
}
