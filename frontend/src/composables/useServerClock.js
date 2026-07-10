import { ref, watch } from 'vue'

// Server clock captured when a list envelope lands — the anchor for
// server−server ages (utils/sessionActivity.serverAgeMs), so a viewer whose
// browser sits in a different timezone than the host never reads recent
// naive host-local stamps as future ("just now"). `extras` is useCursor's
// envelope-sidecar ref; the envelope carries server_now / server_now_utc.
export function useServerClock(extras) {
  const serverClock = ref(null)
  watch(extras, (e) => {
    if (e && e.server_now) {
      serverClock.value = { local: e.server_now, utc: e.server_now_utc, atMs: Date.now() }
    }
  })
  return { serverClock }
}
