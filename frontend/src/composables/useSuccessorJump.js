import { useRoute, useRouter } from 'vue-router'

// Follow a send that was routed to a different session than the page is on.
//
// `/clear` ends the conversation and opens another under a new id — in the
// same tmux pane, or (on the SDK tier) under the same runner. `bridge-send`
// steers through to that one and names it in the response, so the page the
// prompt was typed on is no longer where the answer will appear.
//
// `replace`, not `push`: the cleared id is a dead staging post, and Back
// should not return the operator to a page whose composer now writes
// somewhere else.
export function useSuccessorJump() {
  const route = useRoute()
  const router = useRouter()

  // `from` is the id the composer posted to. Only the page actually showing
  // it may be moved — a send that resolves after the operator has navigated
  // away must not drag them back out of wherever they went.
  function jumpTo(from, to) {
    if (!to || !from || to === from) return
    if (route.params.id !== from) return
    const base = route.name === 'live' ? '/live/' : '/trace/sessions/'
    router.replace(base + to)
  }

  return { jumpTo }
}
