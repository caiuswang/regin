import { ref, computed, watch, onUnmounted } from 'vue'
import { fmtElapsedSeconds } from '../utils/traceFormatters.js'
import { parseLocalIso } from '../utils/sessionActivity.js'

// The one elapsed formula every agent surface shares, anchored to the
// SERVER clock: (server_now − start) + (nowMs − server_now_at). Both halves
// are server−server / phone−phone deltas, so a viewer in a different
// timezone than the server never leaks the offset. NaN when the start is
// unparseable; phone-clock-only fallback for same-TZ dev setups.
export function agentElapsedSeconds(startTime, serverNow, serverNowAt, nowMs) {
  const startMs = startTime ? parseLocalIso(startTime)?.getTime() : NaN
  if (!Number.isFinite(startMs)) return NaN
  const serverNowMs = serverNow ? parseLocalIso(serverNow)?.getTime() : NaN
  const secs = (Number.isFinite(serverNowMs) && serverNowAt)
    ? Math.floor(((serverNowMs - startMs) + (nowMs - serverNowAt)) / 1000)
    : Math.floor((nowMs - startMs) / 1000)
  return Math.max(0, secs)
}

// Ticking elapsed for one running agent. Ticks only while `getActive()` is
// true; a finished agent shows its recorded duration instead, so no tick
// is needed.
export function useAgentElapsed(getStartTime, getServerNow, getServerNowAt, getActive) {
  const nowMs = ref(Date.now())
  let tick = null
  watch(() => !!getActive(), (on) => {
    if (tick) { clearInterval(tick); tick = null }
    if (on) {
      nowMs.value = Date.now()
      tick = setInterval(() => { nowMs.value = Date.now() }, 1000)
    }
  }, { immediate: true })
  onUnmounted(() => { if (tick) clearInterval(tick) })

  return computed(() => {
    const secs = agentElapsedSeconds(
      getStartTime(), getServerNow(), getServerNowAt(), nowMs.value)
    return Number.isFinite(secs) ? fmtElapsedSeconds(secs) : ''
  })
}
