import { watch, onUnmounted } from 'vue'

// While a `compact.pre` exists without a matching later `compact.post`, a
// compaction is in flight in the terminal session. Without a poll, the
// boundary marker upgrade (COMPACTING → COMPACTED) only lands on manual
// refresh because the user is typically parked at the bottom of the trace
// view and no scroll fires the auto-reload latch.
//
// `allSpans` is the shared live span computed; `reload` re-fetches the tail.
// `gates` carries the `reloading`/`loading` refs so a poll tick never stacks
// on an in-flight reload. Self-stops on completion or after a 5-minute cap.
const COMPACT_POLL_MS = 3000
const COMPACT_POLL_MAX_MS = 5 * 60 * 1000

function awaitingCompactPost(spans) {
  if (!spans?.length) return false
  let latestPre = -Infinity
  let latestPost = -Infinity
  for (const s of spans) {
    if (s.name === 'compact.pre') {
      const t = new Date(s.start_time).getTime()
      if (t > latestPre) latestPre = t
    } else if (s.name === 'compact.post') {
      const t = new Date(s.start_time).getTime()
      if (t > latestPost) latestPost = t
    }
  }
  return latestPre > latestPost
}

export function useCompactWatch(allSpans, reload, gates = {}) {
  const { reloading, loading } = gates
  let compactWatchTimer = null
  let compactWatchStartedAt = 0

  function stopCompactWatch() {
    if (compactWatchTimer) {
      clearInterval(compactWatchTimer)
      compactWatchTimer = null
    }
  }

  // Drive `compact.pre → compact.post` polling off the live span set.
  watch(allSpans, (spans) => {
    if (!awaitingCompactPost(spans)) {
      stopCompactWatch()
      return
    }
    if (compactWatchTimer) return
    compactWatchStartedAt = Date.now()
    compactWatchTimer = setInterval(() => {
      if (Date.now() - compactWatchStartedAt > COMPACT_POLL_MAX_MS) {
        stopCompactWatch()
        return
      }
      if (!awaitingCompactPost(allSpans.value)) {
        stopCompactWatch()
        return
      }
      if (reloading?.value || loading?.value) return
      reload()
    }, COMPACT_POLL_MS)
  }, { immediate: true })

  onUnmounted(stopCompactWatch)

  return { stopCompactWatch }
}
