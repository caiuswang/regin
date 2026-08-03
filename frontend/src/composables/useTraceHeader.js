import { shallowRef, onScopeDispose } from 'vue'

// The Trace shell renders a live "N active now" pill, a Refresh button and a
// count on the active tab — all facts only the mounted child view can compute.
// Module scope rather than provide/inject because the shell's <router-view>
// mounts the child as a SIBLING of the header, so the header is not in the
// child's injection path.
const header = shallowRef(null)

export function useTraceHeader() {
  return header
}

// Called by the child view. Patches merge, so a view can publish its counts on
// every load without restating the static parts. The slot is cleared when the
// publishing view's scope dies, so a stale pill can't outlive its tab.
export function useTraceHeaderPublisher() {
  onScopeDispose(() => { header.value = null })
  return (patch) => { header.value = { ...(header.value || {}), ...patch } }
}
