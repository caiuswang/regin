<script setup>
// What sits below the transcript, which is a three-way choice, not two.
// Gathered here because the states are mutually exclusive by construction and
// the host had been spelling the whole ladder inline:
//
//   steerable        → the composer; regin has a terminal it can type into
//   over, reopenable → Resume; the CLI let go of the id and the transcript is
//                      still on disk
//   held elsewhere   → neither, and that is correct: the conversation is alive
//                      on a terminal regin has no handle on (the CLI's
//                      background daemon runs one on a private PTY), so there
//                      is nothing to type into and nothing to resume
//
// The third case is why this is a component. It renders as an empty rail
// otherwise, which reads like a broken page rather than a running session.
import FollowUpComposer from './FollowUpComposer.vue'
import ResumeSessionBar from './ResumeSessionBar.vue'
import HeldSessionBar from './HeldSessionBar.vue'

defineProps({
  sessionId: { type: String, required: true },
  turnNumber: { type: Number, required: true },
  // The follow-up bundle off the session row: { canFollowUp, canResume,
  // heldByCli, cwd, endedReason, model, ctxPct, … }.
  context: { type: Object, default: () => ({}) },
})

// `attached` rides out as `resumed`: both mean the same thing to the host —
// this session just became steerable, reload and the composer takes over.
defineEmits(['sent', 'resumed'])
</script>

<template>
  <FollowUpComposer
    v-if="context.canFollowUp"
    :session-id="sessionId"
    :turn-number="turnNumber"
    :context="context"
    @sent="$emit('sent')"
  />
  <ResumeSessionBar
    v-else-if="context.canResume"
    :session-id="sessionId"
    :context="context"
    @resumed="$emit('resumed')"
  />
  <HeldSessionBar
    v-else-if="context.heldByCli"
    :session-id="sessionId"
    @attached="$emit('resumed')"
  />
</template>
