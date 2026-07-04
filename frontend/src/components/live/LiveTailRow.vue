<script setup>
// One tail row of the /live card. Two shapes (v6 message-first hierarchy):
// message rows (prompt / assistant_response) render an eyebrow + 4-line
// body; everything else is a faint one-line activity micro-row whose dot
// color carries the category (label/color come from the shared utils).
// Built on the <Button> primitive (focus ring, semantics); the .live-row-*
// classes in style.css reshape it into a full-bleed row surface.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import { fmtTime, fmtClock, fmtDuration, toolRowDotClass } from '../../utils/traceFormatters.js'
import { humanMain, isSignal, isHotSpan, rowKind } from '../../utils/liveRows.js'

const props = defineProps({
  span: { type: Object, required: true },
  // Parent is a subagent.start → indent + caret-prefix, like the Terminal log.
  sub: { type: Boolean, default: false },
  // Newest visible row while the session runs → blinking caret.
  caret: { type: Boolean, default: false },
  // Row landed via the live tail → entrance animation (reduced-motion gated).
  entering: { type: Boolean, default: false },
})
const emit = defineEmits(['select'])

const kind = computed(() => rowKind(props.span))
const main = computed(() => humanMain(props.span))
const who = computed(() => (props.span.name === 'prompt' ? 'You' : 'Assistant'))
const dur = computed(() =>
  props.span.duration_ms >= 1000 ? ` · ${fmtDuration(props.span.duration_ms)}` : '')
</script>

<template>
  <Button
    variant="ghost"
    class="live-row"
    :class="[
      kind === 'msg'
        ? ['live-row-msg', span.name === 'prompt' ? 'live-row-msg-user' : '']
        : ['live-row-act', { 'live-row-sub': sub, 'live-row-sys': !isSignal(span) }],
      { 'live-row-entering': entering },
    ]"
    data-testid="live-row"
    :data-kind="kind"
    :data-span-id="span.span_id"
    @click="emit('select', span)"
  >
    <template v-if="kind === 'msg'">
      <!-- Eyebrows are HH:MM (spec v7.2); activity-row times keep seconds. -->
      <span class="live-msg-eyebrow">{{ who }} · {{ fmtTime(span.start_time) }}</span>
      <span class="live-msg-body">{{ main.text }}<span v-if="caret" class="live-caret"></span></span>
    </template>
    <template v-else>
      <span class="live-row-1">
        <span
          class="live-dot"
          :class="[toolRowDotClass(span), { 'live-dot-hot': isHotSpan(span) }]"
        ></span>
        <span v-if="sub" class="live-sub-mark">↳</span>
        <span
          class="live-row-main"
          :class="{ 'live-mono': main.mono, 'live-dim': main.dim }"
        ><span v-if="main.pre" class="live-row-pre">{{ main.pre }}</span>
          {{ main.text }}<span v-if="caret" class="live-caret"></span></span>
        <span class="live-row-dur">{{ fmtClock(span.start_time) }}{{ dur }}</span>
      </span>
    </template>
  </Button>
</template>
