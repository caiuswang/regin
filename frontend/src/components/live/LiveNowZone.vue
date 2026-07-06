<script setup>
// Sticky "now" zone at the /live card's bottom edge — a pure projection of
// the already-loaded tail, one state at a time by priority:
//   permreq-* PENDING → permission  ›  pending-* AskUserQuestion → question
//   › pending-* PENDING tool → tool  ›  promptlive-* PENDING → prompt
//   › session ended → finished
//   › alive + bridge-reachable → idle (full composer; steady dot upstream)
//   › else → latest assistant_response (2-line clamp; [more] opens a sheet).
// The bridge composer (v5) is a CHILD component (LiveComposer) so all async
// send state stays out of this projection: full-width in idle, compact
// steer in response/tool/prompt, absent in question/permission/finished or
// when the bridge is unreachable/disabled.
// A waiting AskUserQuestion is a pending- span like any blocking tool, but it
// gets its own attention state: the question itself, not "running tool.…" —
// and because findLastSpan always reads the NEWEST pending ask, a second
// question cleanly replaces the first once the poll retires its placeholder.
// Placeholders arrive via the same shallow-map window and are retired by the
// serve-time merge — the zone updates on the poll's retired-prune alone.
// Action buttons ([more], [options]) live OUTSIDE the 2-line clamped text —
// a long question/response fills both clamp lines and would clip them.
import { computed, ref, watch, onUnmounted } from 'vue'
import Button from '../ui/Button.vue'
import LiveComposer from './LiveComposer.vue'
import {
  fmtClock, fmtElapsedSeconds, terminalSpanLabel, terminalSpanDetail,
  toolDisplayName,
} from '../../utils/traceFormatters.js'
import { stripMarkdown, findLastSpan, agentStatusLabel } from '../../utils/liveRows.js'
import { parseLocalIso } from '../../utils/sessionActivity.js'
import { useAgentElapsed } from '../../composables/useAgentElapsed.js'

const props = defineProps({
  spans: { type: Array, default: () => [] },
  ended: { type: Boolean, default: false },
  active: { type: Boolean, default: false },
  sessionId: { type: String, default: '' },
  bridgeReachable: { type: Boolean, default: false },
  bridgePane: { type: String, default: '' },
  // Server wall-clock at the last poll (naive local, same basis as span
  // start_time) + the phone-clock ms when it landed. The elapsed anchors to
  // these so a viewer in a different timezone than the server doesn't leak
  // the offset into the readout (see `elapsed`).
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
  // Server phase verdict for THIS zone's agent (main → agent_phase.main;
  // scoped → agent_phase[scopeId]). The zone SELECTS its state from this and
  // only fills CONTENT from the spans — it never re-derives idleness. Empty
  // until the first summary lands (legacy pending-priority fallback then).
  phase: { type: String, default: '' },
  // Segment-aware live-peak ctx% — passed straight through to the composer's
  // bridge row (the second surface for the header's ctx meter).
  ctxPct: { type: Number, default: null },
  // When the tail is scoped to a subagent, the NOW zone shows that agent's
  // status + a way back — the composer is hidden (the bridge only reaches
  // the MAIN agent). null = normal main-scope zone.
  scopeAgent: { type: Object, default: null },
  // Roster counts for the idle sub-note: a subagent's question is invisible
  // to the main projection (its ask is agent-internal), so an idle-looking
  // session with running/waiting agents must say so.
  agentsRunning: { type: Number, default: 0 },
  agentsWaiting: { type: Number, default: 0 },
})
const emit = defineEmits([
  'open-response', 'open-question', 'exit-scope', 'open-agents', 'sent',
])

// Scoped elapsed: same server−server anchor as the main pending-span elapsed,
// so a viewer's timezone never leaks into the agent's running clock.
const scopeElapsed = useAgentElapsed(
  () => props.scopeAgent?.startTime,
  () => props.serverNow,
  () => props.serverNowAt,
  () => props.scopeAgent?.running,
)
// Shared status phrasing (liveRows.agentStatusLabel): running · elapsed,
// finished · duration, interrupted, stale · last seen HH:MM.
const scopeStatus = computed(() => (props.scopeAgent
  ? `agent ${agentStatusLabel(props.scopeAgent, scopeElapsed.value)}`
  : ''))

function isPendingAsk(s) {
  return s.attributes?.tool_name === 'AskUserQuestion'
}
const pendingPerm = computed(() => findLastSpan(props.spans, s =>
  s.status_code === 'PENDING'
  && (s.name === 'permission.request' || (s.span_id || '').startsWith('permreq-'))))
const pendingQuestion = computed(() => findLastSpan(props.spans, s =>
  s.status_code === 'PENDING' && (s.span_id || '').startsWith('pending-')
  && isPendingAsk(s)))
const pendingTool = computed(() => findLastSpan(props.spans, s =>
  s.status_code === 'PENDING' && (s.span_id || '').startsWith('pending-')
  && !isPendingAsk(s)))
const livePrompt = computed(() => findLastSpan(props.spans, s =>
  s.status_code === 'PENDING' && (s.span_id || '').startsWith('promptlive-')))
const lastResponse = computed(() => findLastSpan(props.spans, s =>
  s.name === 'assistant_response' && s.attributes?.text))

// The newest pending placeholder by attention priority — the CONTENT a
// working/blocked state fills itself with. null when no pending is loaded.
const pendingState = computed(() => {
  if (pendingPerm.value) return 'permission'
  if (pendingQuestion.value) return 'question'
  if (pendingTool.value) return 'tool'
  if (livePrompt.value) return 'prompt'
  return null
})

// State SELECTION is the server phase; the spans only fill CONTENT. A
// pending-driven view may win ONLY while the phase says the agent is blocked
// (waiting-*) or working — an inactive/ended session with a leftover pending
// never ticks (kills the "header inactive + ticking tool" contradiction).
const PHASE_STATE = {
  ended: () => 'finished',
  'inactive-stale': () => 'inactive',
  'waiting-permission': () => (pendingPerm.value ? 'permission' : 'response'),
  'waiting-input': () => (pendingQuestion.value ? 'question' : 'response'),
  idle: () => 'idle',
  working: () => (['tool', 'prompt'].includes(pendingState.value)
    ? pendingState.value : 'response'),
}

const state = computed(() => {
  const resolve = PHASE_STATE[props.phase]
  if (resolve) return resolve()
  // No phase yet (pre-first-summary / old payload): legacy pending priority,
  // but never idle (a server verdict) nor a stale finished.
  return pendingState.value || (props.ended ? 'finished' : 'response')
})

// The working spinner / "working…" text belongs to a genuinely-working
// session only. An 'inactive' state (server phase inactive-stale) stays
// neutral even if the client's `active` gate hasn't caught up — the footer
// must never contradict the header's "inactive".
const showWorking = computed(() => state.value === 'response' && props.active)

// idle / inactive → full composer (no live turn to steer into); working
// states → compact steer; question / permission / finished (and bridge
// unreachable/disabled) → none. Staleness ('inactive') is a copy concern
// only — delivery works fine on an inactive-but-bridged session, so the send
// affordance is gated on bridgeReachable, never on the staleness verdict.
const IDLE_LIKE = new Set(['idle', 'inactive'])
const STEERABLE = new Set(['response', 'tool', 'prompt'])
const composerMode = computed(() => {
  // Scoped to a subagent → no composer/steer: the bridge reaches only the
  // main agent, so a steer box here would mislead.
  if (props.scopeAgent) return null
  if (!props.bridgeReachable) return null
  if (IDLE_LIKE.has(state.value)) return 'idle'
  return STEERABLE.has(state.value) ? 'steer' : null
})

// The draft lives HERE (this footer never unmounts) so text typed
// mid-draft survives the composer unmounting — a one-poll reachability
// blip or a question/permission takeover — and reappears on remount.
// It only resets when the view switches sessions.
const composerDraft = ref('')
watch(() => props.sessionId, () => { composerDraft.value = '' })

const permLabel = computed(() => {
  const tool = pendingPerm.value?.attributes?.tool_name
  return `⚠ waiting for permission: ${tool ? `tool.${toolDisplayName(tool)}` : 'tool'}`
})
const permDetail = computed(() => {
  const a = pendingPerm.value?.attributes || {}
  return a.requested_permission || a.command_preview || ''
})
const questionText = computed(() => {
  const qs = pendingQuestion.value?.attributes?.questions || []
  return qs[0]?.question || 'Question for you'
})
const questionMeta = computed(() => {
  const qs = pendingQuestion.value?.attributes?.questions || []
  const opts = (qs[0]?.options || []).length
  const multi = qs.length > 1 ? `question 1 of ${qs.length} · ` : ''
  // 0 options is a legitimate free-text-only ask — "0 options" reads broken.
  const optsLabel = opts ? `${opts} option${opts === 1 ? '' : 's'}` : 'free text'
  return `${multi}${optsLabel}`
})
const responseText = computed(() =>
  stripMarkdown(lastResponse.value?.attributes?.text))

// Live elapsed for the in-flight span: client 1s tick off start_time. The
// interval runs ONLY while a tool/permission state shows it — no idle
// ticking while a response renders or after the session ends.
const nowMs = ref(Date.now())
let tick = null
function stopTick() {
  if (tick) { clearInterval(tick); tick = null }
}
watch(
  () => state.value === 'tool' || state.value === 'permission' || state.value === 'question',
  (needsTick) => {
    stopTick()
    if (needsTick) {
      nowMs.value = Date.now()
      tick = setInterval(() => { nowMs.value = Date.now() }, 1000)
    }
  },
  { immediate: true },
)
onUnmounted(stopTick)

// Newest activity timestamp across the loaded tail — the reference for
// deciding whether a PENDING span is still the live step or has been
// overtaken by fresher spans (a subagent's streaming children, or a newer
// completed tool while an old placeholder was never retired).
const newestSpanTime = computed(() => {
  let mx = 0
  for (const s of props.spans) {
    const t = s.start_time ? (parseLocalIso(s.start_time)?.getTime() || 0) : 0
    if (t > mx) mx = t
  }
  return mx
})
// A genuinely-running tool IS the newest span (delta ≈ 0). A subagent
// parent (tool.Task) or an orphaned placeholder stays PENDING while newer
// spans stream past it — timing those reports the whole subagent runtime /
// a dead placeholder's age, so the clock reads "much bigger than it is".
const STALE_PENDING_MS = 45000

const elapsed = computed(() => {
  const span = pendingPerm.value || pendingQuestion.value || pendingTool.value
  const startMs = span?.start_time ? parseLocalIso(span.start_time)?.getTime() : NaN
  if (!Number.isFinite(startMs)) return ''
  // Suppress the clock when fresher activity exists beyond this span — the
  // elapsed would time a subagent parent / orphan, not the current step.
  // (newestSpanTime and startMs are both server-local, so this delta is
  // timezone-safe.)
  if (newestSpanTime.value - startMs > STALE_PENDING_MS) return ''
  // Anchor "now" to the server's clock, NOT the viewer's: span timestamps
  // are the server's local wall-clock, so a phone in a different timezone
  // subtracting its own Date.now() leaks the offset (a tool reads "4h00m").
  // (server_now − start) is server−server so the offset cancels; the phone
  // delta since server_now landed only makes the readout tick.
  const serverNowMs = props.serverNow
    ? parseLocalIso(props.serverNow)?.getTime() : NaN
  const secs = (Number.isFinite(serverNowMs) && props.serverNowAt)
    ? Math.floor(((serverNowMs - startMs) + (nowMs.value - props.serverNowAt)) / 1000)
    : Math.floor((nowMs.value - startMs) / 1000) // fallback: same-TZ (dev)
  // Shared rollover formatter: a long-running pending tool reads as
  // "8m09s" / "1h05m", never a raw "489s".
  return fmtElapsedSeconds(secs)
})
</script>

<template>
  <footer
    class="live-now"
    :class="{
      'live-now-attention': !scopeAgent && (state === 'permission' || state === 'question'),
      'live-now-idle': !scopeAgent && state === 'idle',
      'live-now-composer': !!composerMode,
      'live-now-scoped': !!scopeAgent,
    }"
    data-testid="live-now"
    :data-state="scopeAgent ? 'scoped' : state"
  >
    <template v-if="scopeAgent">
      <div class="live-now-1">
        <span class="live-now-tag live-now-agent-tag">NOW</span>
        <span
          class="live-now-agent-dot"
          :class="{
            'live-now-agent-dot-live': scopeAgent.running && scopeAgent.status !== 'waiting',
            'live-now-agent-dot-waiting': scopeAgent.status === 'waiting',
          }"
          aria-hidden="true"
        ></span>
        <span class="live-now-label">{{ scopeStatus }}</span>
        <span class="live-now-elapsed">{{ scopeAgent.agentType }}</span>
      </div>
      <!-- back-to-main sits OUTSIDE any clamp (its own action row). -->
      <div class="live-now-act">
        <Button
          variant="link"
          size="sm"
          class="live-now-more"
          data-testid="live-now-back"
          @click="emit('exit-scope')"
        >back to main ↩</Button>
      </div>
    </template>

    <template v-else-if="state === 'permission'">
      <div class="live-now-1">
        <span class="live-now-tag">NOW</span>
        <span class="live-now-label">{{ permLabel }}</span>
        <span class="live-now-elapsed">{{ elapsed }}</span>
      </div>
      <div v-if="permDetail" class="live-now-text live-mono">{{ permDetail }}</div>
    </template>

    <template v-else-if="state === 'question'">
      <div class="live-now-1">
        <span class="live-now-tag">NOW</span>
        <span class="live-now-label">? waiting for your answer</span>
        <span class="live-now-elapsed">{{ elapsed }}</span>
      </div>
      <div class="live-now-text">{{ questionText }}</div>
      <div class="live-now-act">
        <span class="live-now-qmeta">{{ questionMeta }}</span>
        <Button
          variant="link"
          size="sm"
          class="live-now-more"
          data-testid="live-now-options"
          @click="emit('open-question', pendingQuestion)"
        >options ▾</Button>
      </div>
    </template>

    <template v-else-if="state === 'tool'">
      <div class="live-now-1">
        <span class="live-now-tag">NOW</span>
        <span class="live-spinner" aria-hidden="true"></span>
        <span class="live-now-label">running {{ terminalSpanLabel(pendingTool) }}</span>
        <span class="live-now-elapsed">{{ elapsed }}</span>
      </div>
      <div v-if="terminalSpanDetail(pendingTool)" class="live-now-text live-mono">
        {{ terminalSpanDetail(pendingTool) }}
      </div>
    </template>

    <template v-else-if="state === 'prompt'">
      <div class="live-now-1">
        <span class="live-now-tag">NOW</span>
        <span class="live-spinner" aria-hidden="true"></span>
        <span class="live-now-label">processing your prompt…</span>
      </div>
    </template>

    <template v-else-if="state === 'idle'">
      <div class="live-now-1">
        <span class="live-now-tag">NOW</span>
        <span class="live-now-idle-dot" aria-hidden="true"></span>
        <span class="live-now-label">idle — waiting for your prompt</span>
      </div>
      <!-- "idle" is the MAIN agent's truth only — background agents may
           still be working or blocked on a question the main projection
           can't see. Own action row, outside any clamp. -->
      <div v-if="agentsRunning > 0" class="live-now-act">
        <Button
          variant="link"
          size="sm"
          class="live-now-more"
          :class="{ 'live-now-agents-warn': agentsWaiting > 0 }"
          data-testid="live-now-agents-note"
          @click="emit('open-agents')"
        >◈ {{ agentsRunning }} agent{{ agentsRunning === 1 ? '' : 's' }} running{{
          agentsWaiting > 0 ? ` · ${agentsWaiting} waiting` : '' }}</Button>
      </div>
    </template>

    <template v-else>
      <div class="live-now-1">
        <span class="live-now-tag">NOW</span>
        <span
          v-if="!lastResponse && showWorking"
          class="live-spinner"
          aria-hidden="true"
        ></span>
        <span class="live-now-label" :class="{ 'live-now-done': state === 'finished' }">
          {{ state === 'finished' ? '✓ finished'
            : (lastResponse ? 'assistant' : (showWorking ? 'working…' : 'assistant')) }}
        </span>
        <span v-if="lastResponse" class="live-now-elapsed">
          {{ fmtClock(lastResponse.start_time) }}
        </span>
      </div>
      <div class="live-now-text">
        <template v-if="lastResponse">{{ responseText }}</template>
        <!-- Only a genuinely-working session may claim to be working — a
             stale/inactive one must stay neutral or the footer contradicts
             the header's "inactive". -->
        <template v-else-if="showWorking">waiting for the first response…</template>
        <template v-else>no response yet</template>
      </div>
      <div v-if="lastResponse" class="live-now-act">
        <Button
          variant="link"
          size="sm"
          class="live-now-more"
          data-testid="live-now-more"
          @click="emit('open-response', lastResponse)"
        >more ▾</Button>
      </div>
    </template>

    <LiveComposer
      v-if="composerMode"
      v-model:draft="composerDraft"
      :session-id="sessionId"
      :steer="composerMode === 'steer'"
      :pane="bridgePane"
      :ctx-pct="ctxPct"
      @sent="t => emit('sent', t)"
    />
  </footer>
</template>
