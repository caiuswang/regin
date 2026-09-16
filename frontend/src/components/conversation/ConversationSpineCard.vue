<script setup>
// Which card kind one spine row renders, and the two controls that ride on
// top of it (pin, open-in-reader).
//
// Extracted from SessionConversationView, which owns the row WRAPPER — the
// element ref, the tier/run-state data attributes, the nesting and pin
// treatment — because that wrapper is what the feed's scroll, pin and
// cross-highlight machinery reaches for by `data-span-id`. Everything inside
// it is card dispatch, and dispatch is what this file is.
import CondensedRow from './cards/CondensedRow.vue'
import RunningSpanCard from './cards/RunningSpanCard.vue'
import ConversationSpanCard from './ConversationSpanCard.vue'
import SpineDot from './SpineDot.vue'
import SpanRunningTag from '../SpanRunningTag.vue'
import PresentButton from '../present/PresentButton.vue'
import Button from '../ui/Button.vue'
import { runStateOf } from '../../utils/liveRows.js'
import { presentTintFor } from '../../utils/spanColors.js'
import { TIER_CONDENSED, TIER_FOLDED } from './dispatch.js'

const props = defineProps({
  // One built spine row: { span, tier, inAgent, agentSep }.
  row: { type: Object, required: true },
  // The feed's shared render context, taken whole rather than unpacked so this
  // row and the rest of the feed cannot be handed different subsets of it:
  // { selectedSpan, folding, agentMerge, workflowRunsById, pinnedSpanId,
  //   pinnable, keepsCard, bodyOpen }.
  ctx: { type: Object, required: true },
})
defineEmits(['activate', 'present', 'toggle-body', 'toggle-pin', 'enter-scope'])

// The span's OWN in-flight state picks the card kind (a PENDING tool has no
// result to render); `row.runState` may be the agent roster's verdict and
// drives the marks around the card, never the card itself.
const running = () => runStateOf(props.row.span) === 'running'
const pinned = () => props.ctx.pinnedSpanId === props.row.span.span_id
</script>

<template>
  <SpineDot :span="row.span" :state="row.runState || ''" />
  <!-- Above the card, not inside it: every card kind can be in flight, and the
       fallback InlineToolRow is what a PENDING tool actually renders as, so
       one marker on the row covers all of them without 20 edits. The in-flight
       card is the exception — it carries its own pill, and a second badge
       beside it would say the same word twice. The state is the ROW's, not
       the span's: a subagent card is live when its agent is, and the marker
       span itself never says so. -->
  <SpanRunningTag v-if="!running()" :span="row.span" :state="row.runState || ''" class="mb-1 ml-2.5" />
  <Button
    v-if="ctx.pinnable(row.span)"
    variant="ghost"
    size="sm"
    class="spine-pin absolute -left-[30px] top-0 z-10 h-5 w-5 rounded p-0 text-[11px] leading-none transition-opacity hover:bg-transparent"
    :class="pinned() ? 'opacity-100' : 'opacity-0 group-hover/pin:opacity-100 grayscale'"
    :title="pinned() ? 'Unpin from view' : 'Pin to view (hold position across updates)'"
    :aria-pressed="pinned()"
    @click.stop="$emit('toggle-pin', row.span.span_id)"
  >📌</Button>
  <PresentButton
    class="spine-present absolute right-0 top-[3px] z-10"
    :tint="presentTintFor(row.span)"
    @click="$emit('present', row.span)"
  />
  <!-- A span still in flight has no result for the kind switch to render, and
       an empty output panel reads as "finished with no output". -->
  <RunningSpanCard
    v-if="running()"
    :span="row.span"
    :selected-span="ctx.selectedSpan"
    @activate="$emit('activate', $event)"
  />
  <!-- Tier 3 revealed out of a strip stays a dim one-liner and only selects:
       promoting it to a card would let one click quietly undo the mode. -->
  <CondensedRow
    v-else-if="row.tier === TIER_FOLDED"
    dim
    :span="row.span"
    :selected-span="ctx.selectedSpan"
    @activate="$emit('activate', $event)"
  />
  <!-- `manuallyOpen` is applied AFTER the tier, so flipping the mode never
       closes a body the reader opened. A span whose card already rests as a
       one-liner is exempt: it is condensed either way, and only the card has
       somewhere to put what its caret reveals. -->
  <CondensedRow
    v-else-if="row.tier === TIER_CONDENSED && !ctx.keepsCard(row.span)"
    :span="row.span"
    :selected-span="ctx.selectedSpan"
    :expanded="ctx.bodyOpen(row.span.span_id)"
    @activate="$emit('activate', $event)"
    @toggle="$emit('toggle-body', $event)"
  />
  <ConversationSpanCard
    v-else
    :span="row.span"
    :selected-span="ctx.selectedSpan"
    :folding="ctx.folding"
    :agent-merge="ctx.agentMerge"
    :workflow-runs-by-id="ctx.workflowRunsById"
    @activate="$emit('activate', $event)"
    @present="$emit('present', $event)"
    @enter-scope="$emit('enter-scope', $event)"
  />
</template>
