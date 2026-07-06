<script setup>
// Agents sheet body for the /live card. Running agents first (pulsing violet
// dot, agent_type eyebrow, description 2-line clamp, ticking elapsed —
// waiting-for-input agents ride this group with amber treatment); a
// default-collapsed "Finished (N)" group below (muted, result_preview +
// duration) — finished agents are still data users investigate, so collapse,
// don't hide. Interrupted/stale agents (launch denied, or gone silent with
// no subagent.stop ever coming) render in the same group with a muted-amber
// status instead of a duration — still scopeable, never counted as running.
// Primary tap on a card SCOPES the tail to that agent's spans; the chevron
// (its own ≥32px target) opens a curated agent-detail sheet built from the
// ROSTER entry alone (prompt/time/status) — every row gets one, since the
// roster is window-independent and the start span may not be loaded yet.
import { computed, ref, watch, onUnmounted } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import { fmtDuration, fmtElapsedSeconds } from '../../utils/traceFormatters.js'
import { agentStatusLabel } from '../../utils/liveRows.js'
import { agentElapsedSeconds } from '../../composables/useAgentElapsed.js'

const props = defineProps({
  runningAgents: { type: Array, default: () => [] },
  finishedAgents: { type: Array, default: () => [] },
  // Server wall-clock at the last poll + the phone-clock ms when it landed —
  // the running elapsed is (server_now − start) + (now − server_now_at), a
  // server−server delta so a viewer's timezone never leaks in.
  serverNow: { type: String, default: '' },
  serverNowAt: { type: Number, default: 0 },
})
const emit = defineEmits(['view-agent', 'scope'])

const finishedOpen = ref(false)
const isEmpty = computed(() =>
  !props.runningAgents.length && !props.finishedAgents.length)

const nowMs = ref(Date.now())
let tick = null
watch(() => props.runningAgents.length > 0, (needsTick) => {
  if (tick) { clearInterval(tick); tick = null }
  if (needsTick) {
    nowMs.value = Date.now()
    tick = setInterval(() => { nowMs.value = Date.now() }, 1000)
  }
}, { immediate: true })
onUnmounted(() => { if (tick) clearInterval(tick) })

function elapsedOf(agent) {
  const secs = agentElapsedSeconds(
    agent.startTime, props.serverNow, props.serverNowAt, nowMs.value)
  return Number.isFinite(secs) ? fmtElapsedSeconds(secs) : ''
}
</script>

<template>
  <div data-testid="live-agent-sheet">
    <div v-if="isEmpty" class="live-sheet-empty" data-testid="live-agent-empty">
      no agents launched this session
    </div>

    <template v-else>
      <p class="live-sheet-hint">Tap an agent to scope the tail to its spans · › opens agent detail</p>
      <div v-if="!runningAgents.length" class="live-sheet-empty">no agents running</div>
      <div v-for="a in runningAgents" :key="a.spanId" class="live-agent-row">
        <Button
          variant="ghost"
          class="live-agent-card"
          data-testid="live-agent-card"
          :aria-label="`Scope the tail to ${a.agentType}`"
          @click="emit('scope', a)"
        >
          <span
            class="live-agent-dot"
            :class="a.status === 'waiting' ? 'live-agent-dot-waiting' : 'live-agent-dot-live'"
            aria-hidden="true"
          ></span>
          <span class="live-agent-main">
            <span class="live-agent-eyebrow">{{ a.agentType }} · {{ a.startClock }}</span>
            <span class="live-agent-desc">{{ a.description }}</span>
          </span>
          <span
            v-if="a.status === 'waiting'"
            class="live-agent-time live-agent-time-warn"
            data-testid="live-agent-status"
          >{{ agentStatusLabel(a, '', { compact: true }) }}</span>
          <span v-else class="live-agent-time">{{ agentStatusLabel(a, elapsedOf(a)) }}</span>
        </Button>
        <!-- Roster-sourced detail: works even when the row's start marker
             isn't in the loaded tail window — every row gets the affordance. -->
        <Button
          variant="ghost"
          size="icon"
          class="live-agent-info"
          data-testid="live-agent-info"
          :aria-label="`Agent details for ${a.agentType}`"
          @click="emit('view-agent', a)"
        >
          <Icon name="chevron-right" :size="16" />
        </Button>
      </div>

      <template v-if="finishedAgents.length">
        <Button
          variant="ghost"
          class="live-disclosure"
          data-testid="live-agent-finished-toggle"
          :aria-expanded="finishedOpen"
          @click="finishedOpen = !finishedOpen"
        >
          <span class="live-disclosure-tw" :class="{ 'live-disclosure-open': finishedOpen }">▸</span>
          Finished ({{ finishedAgents.length }})
        </Button>
        <div v-if="finishedOpen" data-testid="live-agent-finished">
          <div v-for="a in finishedAgents" :key="a.spanId" class="live-agent-row">
            <Button
              variant="ghost"
              class="live-agent-card live-agent-card-done"
              data-testid="live-agent-card"
              :aria-label="`Scope the tail to ${a.agentType}`"
              @click="emit('scope', a)"
            >
              <span class="live-agent-dot" aria-hidden="true"></span>
              <span class="live-agent-main">
                <span class="live-agent-eyebrow">{{ a.agentType }} · {{ a.startClock }}</span>
                <span class="live-agent-desc">{{ a.resultPreview || a.description }}</span>
              </span>
              <!-- compact: the verbose "stale · last seen HH:MM" squeezes
                   the description to ~10ch in this column at 375px. -->
              <span
                v-if="a.status === 'interrupted' || a.status === 'stale'"
                class="live-agent-time live-agent-time-warn"
                data-testid="live-agent-status"
              >{{ agentStatusLabel(a, '', { compact: true }) }}</span>
              <!-- Always render an outcome: real stop markers are point
                   events (duration_ms 0), so a falsy derived duration still
                   reads "finished", never an empty slot. -->
              <span v-else class="live-agent-time" data-testid="live-agent-status">
                {{ fmtDuration(a.durationMs) || 'finished' }}
              </span>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              class="live-agent-info"
              data-testid="live-agent-info"
              :aria-label="`Agent details for ${a.agentType}`"
              @click="emit('view-agent', a)"
            >
              <Icon name="chevron-right" :size="16" />
            </Button>
          </div>
        </div>
      </template>
    </template>
  </div>
</template>
