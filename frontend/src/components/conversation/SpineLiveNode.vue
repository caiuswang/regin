<script setup>
// The node at the end of the spine that says what the session is doing NOW,
// and opens the real terminal when you click it.
//
// It exists because the obvious anchor does not work. `SpanRunningTag` marks
// rows whose span is PENDING, and a span is only PENDING while a tool is in
// flight — so hanging "look at the real terminal" off it reproduced the very
// bug this whole feature removes: during extended thinking, a streamed
// response, or any hook-silent stretch there is no PENDING row at all, and the
// affordance vanished exactly when the transcript had least to say.
//
// So this node is anchored to the SESSION, not to a span. It is present
// whenever EITHER tier can say something about it, and it fills with that
// tier's own status (lib/agent_bridge/screen_state.py) whenever the verdict
// has one. A null `screen` is "no verdict", never "idle" — the copy says so
// rather than guessing.
//
// Status and terminal are two gates, not one. A run regin launched over the
// SDK reports a real status through `screen` while having no tmux pane at
// all, so gating the whole node on the pane blanked the only thing telling
// the operator what such a session was doing. The pane gates the button; the
// verdict gates the row.
//
// Where there IS a pane the WHOLE row is the button, not an icon inside it: a
// 18px icon-only target was both hard to find and hard to hit. The status text
// is the label, which also makes what you get when you click it self-evident.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import TraceTerminalPeek from '../trace/TraceTerminalPeek.vue'
import { fmtElapsedSeconds } from '../../utils/traceFormatters.js'

// One bundle, one binding: the host template is a few directives under its
// budget and every consumer of this node pays for its own props there.
const props = defineProps({
  live: { type: Object, default: null },
})

const session = computed(() => props.live?.bridgeSession || null)
const screen = computed(() => props.live?.screen || null)
const busy = computed(() => screen.value?.busy === true)
// A retrying turn is still busy, so the verb form would render a plain
// `Working… 6m18s` over a session that is stalled on a failing API call —
// the exact spinner-with-no-explanation this node exists to end.
// `retry_final` is the `retrying once` frame: a retry is in flight but the
// screen names no attempt number, so it is a separate signal rather than a
// missing one.
const retrying = computed(() =>
  typeof screen.value?.retry_attempt === 'number' || screen.value?.retry_final === true)
// Same `data-run-state` axis the spine already colours its dots and row tints
// on — a retry is still running, so it keeps the halo and swaps the hue.
const runState = computed(() => {
  if (retrying.value) return 'error'
  return busy.value ? 'running' : undefined
})

const label = computed(() => {
  if (!screen.value) return 'status unavailable'
  if (retrying.value) {
    const s = screen.value
    const head = s.retry_label || 'API error'
    if (typeof s.retry_attempt !== 'number') return `${head} — retrying, final attempt`
    const of = typeof s.retry_max === 'number' ? `/${s.retry_max}` : ''
    return `${head} — retrying, attempt ${s.retry_attempt}${of}`
  }
  if (!screen.value.busy) return 'at the prompt'
  // The spec's copy for a live strip with nothing more specific to say. Where
  // the terminal DOES name a verb, keep it — that is strictly more than the
  // placeholder, and the copy deck is a floor, not a ceiling.
  const verb = screen.value.verb ? `${screen.value.verb}…` : 'Computing…'
  const secs = screen.value.elapsed_sec
  return typeof secs === 'number' ? `${verb} ${fmtElapsedSeconds(secs)}` : verb
})
</script>

<template>
  <div
    v-if="session || screen"
    class="event-spine event-spine-row spine-live-row"
    :class="{ 'spine-row-run': busy }"
    :data-run-state="runState"
    data-testid="spine-live-node"
  >
    <span
      class="spine-dot"
      :class="busy ? 'spine-dot-run' : 'spine-dot-live-idle'"
      :data-run-state="runState"
      aria-hidden="true"
    ><span class="spine-dot-core"></span></span>
    <TraceTerminalPeek v-if="session" :session="session">
      <template #trigger>
        <Button
          variant="ghost"
          size="sm"
          class="spine-live"
          title="Open the session's real terminal"
          data-testid="spine-terminal-btn"
        >
          <span class="spine-live-label">{{ label }}</span>
          <span v-if="busy && screen.tool" class="spine-live-tool">{{ screen.tool }}</span>
          <span class="spine-live-cta">
            <Icon name="terminal" :size="13" />
            open terminal
          </span>
        </Button>
      </template>
    </TraceTerminalPeek>
    <div v-else class="spine-live spine-live-flat" data-testid="spine-live-status">
      <span class="spine-live-label">{{ label }}</span>
      <span v-if="busy && screen.tool" class="spine-live-tool">{{ screen.tool }}</span>
    </div>
  </div>
</template>
