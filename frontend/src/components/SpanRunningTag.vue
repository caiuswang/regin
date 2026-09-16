<script setup>
// The "this has not finished" marker for one span, shared by every desktop
// projection of the feed (conversation spine, timeline spine) so a reader who
// switches view mode does not lose the one row that is still moving.
//
// Three states, never one: a running tool is the agent moving on its own, a
// parked question is blocked on the reader, and a queued call is one the
// model asked for that the harness has not started. They get emerald, amber
// and slate respectively, following the attention ramp the session switcher
// states — amber is the only colour allowed to demand action, so it must
// stay scarce, and queued must not read as motion at all.
//
// `folded` is the same two states seen from a COLLAPSED ancestor: the work is
// real but it is not this row's own, so it renders as an outline rather than a
// fill. Without it a collapsed tree node hides the only row still moving, and
// the marker is worth least exactly when the reader cannot see the row.
import { computed } from 'vue'
import { runStateOf } from '../utils/liveRows.js'

const props = defineProps({
  span: { type: Object, default: null },
  // Explicit override for a caller that derived the state itself (a collapsed
  // subtree rollup). Falls back to the span's own state.
  state: { type: String, default: '' },
  folded: { type: Boolean, default: false },
})

const resolved = computed(() => props.state || runStateOf(props.span))
const LABEL = { running: 'running', waiting: 'waiting on you', queued: 'queued' }
const FOLDED_LABEL = { running: 'running inside', waiting: 'waiting inside', queued: 'queued inside' }
const TITLE = {
  running: 'this step has not reported back yet — the agent is still working on it',
  waiting: 'the agent is parked on this and cannot continue until you answer',
  queued: 'the agent asked for this call, but the harness has not started it yet',
}
const FOLDED_TITLE = {
  running: 'something inside this collapsed subtree is still running — expand to see it',
  waiting: 'something inside this collapsed subtree is waiting on you — expand to see it',
  queued: 'something inside this collapsed subtree is queued behind a running call',
}
const label = computed(() => (props.folded ? FOLDED_LABEL : LABEL)[resolved.value])
const title = computed(() => (props.folded ? FOLDED_TITLE : TITLE)[resolved.value])
</script>

<template>
  <span
    v-if="resolved"
    class="span-running-tag"
    :class="[`span-running-tag--${resolved}`, folded ? 'span-running-tag--folded' : '']"
    :data-run-state="resolved"
    :data-run-folded="folded || undefined"
    data-testid="span-running-tag"
    :title="title"
  >
    <span class="span-running-pip" aria-hidden="true"></span>
    {{ label }}
  </span>
</template>
