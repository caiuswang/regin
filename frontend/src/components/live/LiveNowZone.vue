<script setup>
// Sticky "now" zone at the /live card's bottom edge — a pure projection of
// the already-loaded tail, one state at a time by priority:
//   permreq-* PENDING → permission  ›  pending-* PENDING tool → tool
//   › promptlive-* PENDING → prompt  ›  session ended → finished
//   › else → latest assistant_response (2-line clamp; [more] opens a sheet).
// Placeholders arrive via the same shallow-map window and are retired by the
// serve-time merge — the zone updates on the poll's retired-prune alone.
import { computed, ref, watch, onUnmounted } from 'vue'
import Button from '../ui/Button.vue'
import {
  fmtClock, terminalSpanLabel, terminalSpanDetail, toolDisplayName,
} from '../../utils/traceFormatters.js'
import { stripMarkdown, findLastSpan } from '../../utils/liveRows.js'

const props = defineProps({
  spans: { type: Array, default: () => [] },
  ended: { type: Boolean, default: false },
})
const emit = defineEmits(['open-response'])

const pendingPerm = computed(() => findLastSpan(props.spans, s =>
  s.status_code === 'PENDING'
  && (s.name === 'permission.request' || (s.span_id || '').startsWith('permreq-'))))
const pendingTool = computed(() => findLastSpan(props.spans, s =>
  s.status_code === 'PENDING' && (s.span_id || '').startsWith('pending-')))
const livePrompt = computed(() => findLastSpan(props.spans, s =>
  s.status_code === 'PENDING' && (s.span_id || '').startsWith('promptlive-')))
const lastResponse = computed(() => findLastSpan(props.spans, s =>
  s.name === 'assistant_response' && s.attributes?.text))

const state = computed(() => {
  if (pendingPerm.value) return 'permission'
  if (pendingTool.value) return 'tool'
  if (livePrompt.value) return 'prompt'
  if (props.ended) return 'finished'
  return 'response'
})

const permLabel = computed(() => {
  const tool = pendingPerm.value?.attributes?.tool_name
  return `⚠ waiting for permission: ${tool ? `tool.${toolDisplayName(tool)}` : 'tool'}`
})
const permDetail = computed(() => {
  const a = pendingPerm.value?.attributes || {}
  return a.requested_permission || a.command_preview || ''
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
  () => state.value === 'tool' || state.value === 'permission',
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
const elapsed = computed(() => {
  const span = pendingPerm.value || pendingTool.value
  if (!span?.start_time) return ''
  const secs = Math.floor((nowMs.value - new Date(span.start_time).getTime()) / 1000)
  return secs >= 0 ? `${secs}s` : ''
})
</script>

<template>
  <footer
    class="live-now"
    :class="{ 'live-now-attention': state === 'permission' }"
    data-testid="live-now"
    :data-state="state"
  >
    <template v-if="state === 'permission'">
      <div class="live-now-1">
        <span class="live-now-tag">NOW</span>
        <span class="live-now-label">{{ permLabel }}</span>
        <span class="live-now-elapsed">{{ elapsed }}</span>
      </div>
      <div v-if="permDetail" class="live-now-text live-mono">{{ permDetail }}</div>
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

    <template v-else>
      <div class="live-now-1">
        <span class="live-now-tag">NOW</span>
        <span class="live-now-label" :class="{ 'live-now-done': state === 'finished' }">
          {{ state === 'finished' ? '✓ finished' : 'assistant' }}
        </span>
        <span v-if="lastResponse" class="live-now-elapsed">
          {{ fmtClock(lastResponse.start_time) }}
        </span>
      </div>
      <div class="live-now-text">
        <template v-if="lastResponse">
          {{ responseText }}
          <Button
            variant="link"
            size="sm"
            class="live-now-more"
            data-testid="live-now-more"
            @click="emit('open-response', lastResponse)"
          >more ▾</Button>
        </template>
        <template v-else>no response yet</template>
      </div>
    </template>
  </footer>
</template>
