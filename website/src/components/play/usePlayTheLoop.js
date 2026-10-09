import { computed, onUnmounted, reactive } from 'vue'

import { ACTION_LABELS, P_DEFECTS, P_LEDGER, P_STAGES, P_TASKS, P_TIMELINE, PT } from '../../content/play.js'

const LAST_STAGE = P_STAGES.length - 1
const LAST_TICK = P_TIMELINE.length - 1

const initialProgress = () => ({
  typed: 0, specced: false, shown: 0, tick: 0, fixed: P_DEFECTS.map(() => false), lines: 0,
})

// G9: under reduced motion a timer completes in one step instead of animating.
const reducedMotion = () => typeof window !== 'undefined'
  && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// Each timed stage: the progress field it advances, its ceiling, and the step interval (ms).
const TIMED = {
  0: { field: 'typed', max: PT.length, ms: 70 },
  1: { field: 'shown', max: P_TASKS.length, ms: 180 },
  2: { field: 'tick', max: LAST_TICK, ms: 650 },
  4: { field: 'lines', max: P_LEDGER.length, ms: 160 },
}

export function usePlayTheLoop() {
  const st = reactive({ stage: 0, reached: 0, busy: false, ...initialProgress() })
  let iv = null

  const stopIv = () => { clearInterval(iv); iv = null }

  function runTimed({ field, max, ms }) {
    if (reducedMotion()) { st[field] = max; return }
    stopIv()
    st.busy = true
    iv = setInterval(() => {
      if (st[field] >= max) { stopIv(); st.busy = false; return }
      st[field] += 1
    }, ms)
  }

  const stageDone = (s) => [
    st.specced, st.shown >= P_TASKS.length, st.tick >= LAST_TICK, st.fixed.every(Boolean), st.lines >= P_LEDGER.length,
  ][s]

  function fix(i) {
    if (i < 0 || st.fixed[i]) return
    st.fixed[i] = true
  }

  function goStage(i) {
    if (i > st.reached || st.busy) return
    st.stage = i
  }

  function next() {
    st.stage += 1
    st.reached = Math.max(st.reached, st.stage)
  }

  function act() {
    const s = st.stage
    if (st.busy) return
    if (s === LAST_STAGE && stageDone(s)) {
      Object.assign(st, { stage: 0, reached: 0, ...initialProgress() })
      return
    }
    if (stageDone(s)) return next()
    if (s === 0 && st.typed >= PT.length) { st.specced = true; return }
    if (s === 3) return fix(st.fixed.indexOf(false))
    runTimed(TIMED[s])
  }

  onUnmounted(stopIv)

  const done = computed(() => stageDone(st.stage))

  const actionLabel = computed(() => {
    if (st.busy) return '· · ·'
    if (done.value) return st.stage === LAST_STAGE ? '↺ PLAY AGAIN' : `NEXT: ${P_STAGES[st.stage + 1].label} ▶`
    if (st.stage === 0 && st.typed >= PT.length) return 'WRITE SPEC'
    return ACTION_LABELS[st.stage]
  })

  const track = computed(() => P_STAGES.map((m, i) => {
    const current = i === st.stage
    const ok = i < st.stage || (current && done.value) || i < st.reached
    return { label: m.label, code: `0-${i + 1}`, mark: (ok ? '★ ' : '') + `0-${i + 1}`, current, ok, locked: i > st.reached }
  }))

  const hud = computed(() => ({
    stageNo: st.stage + 1,
    doneCount: `${P_TIMELINE[st.tick][1].length}/7`,
    openDefects: st.stage >= 3 ? st.fixed.filter((f) => !f).length : 0,
  }))

  return { st, act, goStage, fix, done, actionLabel, track, hud, meta: computed(() => P_STAGES[st.stage]) }
}
